"""每日盤前快報 — 抓取美股夜盤 + 台指期 + VIX，推播到 Telegram。

用法（由 supercronic 透過 crontab 執行）：
    python morning_brief.py

資料來源：
    - 美股（S&P 500、NASDAQ、費半、VIX）：yfinance
    - 台指期：yfinance（TX=F 或 WTX=F），失敗時回退至 TAIFEX 公開 API
"""

import os
import sys
from datetime import datetime

import requests
import yfinance as yf
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    import alert_config
except ImportError:
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        import alert_config_cloud as alert_config
    else:
        raise SystemExit(
            "找不到 alert_config.py——複製 alert_config.example.py 成 alert_config.py,"
            "填入你的 Telegram Bot Token/Chat ID 後再執行一次。"
        )

# ──────────────────────────────────────────────────────────────────────
# 美股指數代碼
US_SYMBOLS = {
    "^GSPC": "S&P 500",
    "^IXIC": "NASDAQ",
    "^SOX": "費半",
    "^VIX": "VIX",
}

# 台指期代碼（依序嘗試）
TW_FUTURES_SYMBOLS = ["TX=F", "WTX=F"]

# Telegram session
_tg_session: requests.Session | None = None


def _get_tg_session() -> requests.Session:
    global _tg_session
    if _tg_session is None:
        _tg_session = requests.Session()
        retry = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        _tg_session.mount("https://", adapter)
    return _tg_session


def send_telegram_message(text: str) -> bool:
    """發送 Telegram 訊息，成功回傳 True。"""
    url = f"https://api.telegram.org/bot{alert_config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        session = _get_tg_session()
        resp = session.post(
            url,
            json={"chat_id": alert_config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"[morning_brief] Telegram 推播失敗: {resp.status_code} {resp.text}")
            return False
        return True
    except Exception as e:
        print(f"[morning_brief] Telegram 推播失敗: {e}")
        return False


# ──────────────────────────────────────────────────────────────────────
# 抓取美股指數
def fetch_us_indices() -> dict[str, dict]:
    """用 yfinance 抓美股指數現價與前收盤，計算漲跌幅。"""
    results = {}
    for symbol, label in US_SYMBOLS.items():
        try:
            ticker = yf.Ticker(symbol)
            # 取最近 2 天盤後資料以涵蓋夜盤
            hist = ticker.history(period="2d", auto_adjust=False)
            if hist is None or hist.empty:
                results[label] = {"last": None, "prev_close": None, "change_pct": None}
                continue
            last = float(hist["Close"].iloc[-1])
            if len(hist) >= 2:
                prev_close = float(hist["Close"].iloc[-2])
                change_pct = (last - prev_close) / prev_close * 100 if prev_close else None
            else:
                prev_close = None
                change_pct = None
            results[label] = {
                "last": last,
                "prev_close": prev_close,
                "change_pct": change_pct,
            }
        except Exception as e:
            print(f"[morning_brief] {label} ({symbol}) 抓取失敗: {e}")
            results[label] = {"last": None, "prev_close": None, "change_pct": None}
    return results


# ──────────────────────────────────────────────────────────────────────
# 抓取台指期（TX）
def fetch_twse_futures_yfinance() -> dict | None:
    """用 yfinance 嘗試抓台指期。成功回傳 dict，失敗回傳 None。"""
    for symbol in TW_FUTURES_SYMBOLS:
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="2d", auto_adjust=False)
            if hist is None or hist.empty:
                continue
            last = float(hist["Close"].iloc[-1])
            if len(hist) >= 2:
                prev_close = float(hist["Close"].iloc[-2])
                change_pts = last - prev_close if prev_close else None
            else:
                prev_close = None
                change_pts = None
            return {
                "symbol": symbol,
                "label": "台指期",
                "last": last,
                "prev_close": prev_close,
                "change_pts": change_pts,
                "source": "yfinance",
            }
        except Exception as e:
            print(f"[morning_brief] 台指期 ({symbol}) yfinance 抓取失敗: {e}")
    return None


def fetch_twse_futures_taifex() -> dict | None:
    """從 TAIFEX 公開 API 抓台指期（TX）最近交易日資料。"""
    try:
        # TAIFEX 公開 API — 取得台指期（TX）最近交易日收盤
        url = "https://www.taifex.com.tw/cht/3/futContractsDate"
        # 使用每日交易資訊 API
        resp = requests.get(
            "https://www.taifex.com.tw/cht/3/futContractsDateDown",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        # 若上者失敗，改抓每日行情 CSV
        if resp.status_code != 200:
            return None

        # 解析 CSV（逗號分隔，欄位含契約、到期月份等）
        text = resp.text
        lines = text.strip().split("\n")
        # 找 TX 最近一筆收盤價
        for line in lines:
            if line.startswith("TX,"):
                parts = line.split(",")
                # 收盤價在第 6 欄（依 TAIFEX CSV 格式）
                if len(parts) >= 6:
                    try:
                        close_price = float(parts[5])
                        return {
                            "label": "台指期",
                            "last": close_price,
                            "prev_close": None,
                            "change_pts": None,
                            "source": "taifex",
                        }
                    except (ValueError, IndexError):
                        continue
        return None
    except Exception as e:
        print(f"[morning_brief] 台指期 TAIFEX 抓取失敗: {e}")
        return None


def fetch_twse_futures() -> dict:
    """依序嘗試 yfinance → TAIFEX，取得台指期報價。"""
    result = fetch_twse_futures_yfinance()
    if result:
        return result
    result = fetch_twse_futures_taifex()
    if result:
        return result
    return {"label": "台指期", "last": None, "prev_close": None, "change_pts": None, "source": "none"}


# ──────────────────────────────────────────────────────────────────────
# 格式化訊息
def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def _fmt_pts(value: float | None) -> str:
    if value is None:
        return "N/A"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.1f} 點"


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}"


def _generate_highlights(us: dict, futures: dict) -> str:
    """根據市場狀態自動產生重點摘要。"""
    highlights = []
    spx = us.get("S&P 500", {}).get("change_pct")
    ndx = us.get("NASDAQ", {}).get("change_pct")
    vix = us.get("VIX", {}).get("last")
    fut_pts = futures.get("change_pts")

    if spx is not None:
        if spx >= 1.5:
            highlights.append("美股大漲，多方強勢")
        elif spx >= 0.5:
            highlights.append("美股偏多，穩步走高")
        elif spx >= -0.5:
            highlights.append("美股平盤震盪，觀望")
        elif spx >= -1.5:
            highlights.append("美股拉回，注意支撐")
        else:
            highlights.append("美股大跌，留意風險")

    if ndx is not None and abs(ndx - (spx or 0)) > 1.0:
        highlights.append("科技股走勢分歧於大盤")

    if vix is not None:
        if vix >= 25:
            highlights.append(f"VIX 飆升至 {vix:.1f}，市場恐慌")
        elif vix >= 20:
            highlights.append(f"VIX {vix:.1f}，避險情緒升溫")
        elif vix <= 13:
            highlights.append(f"VIX {vix:.1f}，市場偏樂觀")

    if fut_pts is not None:
        if fut_pts >= 100:
            highlights.append("台指期大幅走高")
        elif fut_pts >= 30:
            highlights.append("台指期偏多")
        elif fut_pts <= -100:
            highlights.append("台指期大幅走弱")
        elif fut_pts <= -30:
            highlights.append("台指期偏空")

    if not highlights:
        highlights.append("市場觀望，留意國際情勢")

    return "、".join(highlights)


def format_message(us: dict, futures: dict) -> str:
    """組合成最終推播訊息。"""
    spx = us.get("S&P 500", {})
    ndx = us.get("NASDAQ", {})
    sox = us.get("費半", {})
    vix = us.get("VIX", {})

    highlights = _generate_highlights(us, futures)

    fut_pts_str = _fmt_pts(futures.get("change_pts"))
    fut_last_str = _fmt_price(futures.get("last"))

    msg_lines = [
        f"☀️ 盤前快報 08:00",
        f"",
        f"美股：",
        f"  S&P {_fmt_pct(spx.get('change_pct'))}｜NASDAQ {_fmt_pct(ndx.get('change_pct'))}｜",
        f"  費半 {_fmt_pct(sox.get('change_pct'))}｜VIX {_fmt_price(vix.get('last'))}",
        f"",
        f"台指期：{fut_pts_str}（{fut_last_str}）",
        f"",
        f"📌 {highlights}",
    ]
    return "\n".join(msg_lines)


# ──────────────────────────────────────────────────────────────────────
# 主程式
def main() -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[morning_brief] 開始執行 {now}")

    # 抓取資料
    us = fetch_us_indices()
    futures = fetch_twse_futures()

    # 格式化
    message = format_message(us, futures)
    print(f"[morning_brief] 訊息內容:\n{message}")

    # 推播
    success = send_telegram_message(message)
    if success:
        print("[morning_brief] ✅ 推播成功")
    else:
        print("[morning_brief] ❌ 推播失敗")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
