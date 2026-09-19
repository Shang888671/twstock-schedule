"""訊號牆 — 每日盤後掃描自選股技術訊號並推播 Telegram。

使用方式：
1. 手動跑：python signals_wall.py
2. fly.io crontab：30 7 * * 1-5（UTC 07:30 = 台北 15:30）

流程：
1. 讀取 WATCH_LIST（alert_config_cloud.py）
2. 用 yfinance 抓 60 日歷史（SQLite 快取）
3. 計算技術面訊號（突破月線、KD 黃金交叉、MACD 翻多、放量、RSI 超賣）
4. 有觸發訊號就推播 Telegram

訊號定義（參考 skills/stock-basic/signals-wall.md）：
- 突破月線：昨天收盤 ≤ MA20，今天收盤 > MA20
- KD 黃金交叉：K 上穿 D（昨天 K ≤ D，今天 K > D）
- MACD 翻多：DIF 由負轉正（昨天 DIF ≤ 0，今天 DIF > 0）
- 放量：今日成交量 > 5 日均量 × 1.5
- RSI 超賣：RSI(14) < 30
"""

import sys
import time
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from alert_config_cloud import WATCH_LIST, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ALERT_ENABLED
import fetch_data
from database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


def calculate_ma(series: pd.Series, period: int) -> pd.Series:
    """計算簡單移動平均。"""
    return series.rolling(window=period, min_periods=period).mean()


def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """計算 RSI。"""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calculate_kd(high: pd.Series, low: pd.Series, close: pd.Series,
                 n: int = 9, m: int = 3, m2: int = 3):
    """計算 KD 指標。回傳 (K, D)。

    使用台股看盤軟體標準公式：
    RSV = (Close - LowestLow(n)) / (HighestHigh(n) - LowestLow(n)) * 100
    K = EMA(RSV, alpha=1/m)
    D = EMA(K, alpha=1/m2)
    """
    lowest_low = low.rolling(window=n, min_periods=n).min()
    highest_high = high.rolling(window=n, min_periods=n).max()
    rsv = (close - lowest_low) / (highest_high - lowest_low) * 100
    k = rsv.ewm(alpha=1/m, adjust=False).mean()
    d = k.ewm(alpha=1/m2, adjust=False).mean()
    return k, d


def calculate_macd(close: pd.Series, fast: int = 12, slow: int = 26,
                   signal: int = 9):
    """計算 MACD。回傳 (DIF, DEA, MACD_hist)。"""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd_hist = 2 * (dif - dea)
    return dif, dea, macd_hist


def check_signals(df: pd.DataFrame) -> list[str]:
    """檢查技術面訊號。回傳觸發的訊號列表。

    需要至少 30 日資料（MACD slow EMA 26 日 + 緩衝）。
    """
    if len(df) < 30:
        return []

    close = df["Close"]
    volume = df["Volume"]
    high = df["High"]
    low = df["Low"]

    signals = []

    # 1. 突破月線（MA20）
    # 昨天收盤 ≤ MA20，今天收盤 > MA20
    ma20 = calculate_ma(close, 20)
    if pd.notna(ma20.iloc[-2]) and pd.notna(ma20.iloc[-1]):
        if close.iloc[-2] <= ma20.iloc[-2] and close.iloc[-1] > ma20.iloc[-1]:
            signals.append("突破月線↑")

    # 2. KD 黃金交叉
    # K 上穿 D：昨天 K ≤ D，今天 K > D
    k, d = calculate_kd(high, low, close)
    if pd.notna(k.iloc[-2]) and pd.notna(d.iloc[-2]):
        if k.iloc[-2] <= d.iloc[-2] and k.iloc[-1] > d.iloc[-1]:
            signals.append("KD 黃金交叉↑")

    # 3. MACD 翻多（DIF 由負轉正）
    # 昨天 DIF ≤ 0，今天 DIF > 0
    dif, dea, macd_hist = calculate_macd(close)
    if pd.notna(dif.iloc[-2]) and pd.notna(dif.iloc[-1]):
        if dif.iloc[-2] <= 0 and dif.iloc[-1] > 0:
            signals.append("MACD 翻多↑")

    # 4. 量大於 5 日均量 1.5 倍
    vol_ma5 = calculate_ma(volume, 5)
    if pd.notna(vol_ma5.iloc[-1]) and vol_ma5.iloc[-1] > 0:
        ratio = volume.iloc[-1] / vol_ma5.iloc[-1]
        if ratio >= 1.5:
            signals.append(f"放量 {ratio:.1f}x↑")

    # 5. RSI 低於 30 超賣
    rsi = calculate_rsi(close)
    if pd.notna(rsi.iloc[-1]) and rsi.iloc[-1] < 30:
        signals.append(f"RSI {rsi.iloc[-1]:.0f} 超賣↑")

    return signals


def send_telegram(message: str) -> bool:
    """發送 Telegram 訊息。"""
    import requests

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code != 200:
            logger.error(f"Telegram API 錯誤：{resp.status_code} {resp.text}")
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Telegram 發送失敗：{e}")
        return False


def scan_watchlist() -> list[dict]:
    """掃描整個 watchlist，回傳觸發訊號的股票。"""
    init_db()

    triggered = []
    today = date.today()

    for stock in WATCH_LIST:
        code = stock["code"]
        label = stock["label"]
        otc = stock.get("otc", False)

        # 跳過指數（t00 加權指數 / o00 櫃買指數）
        if code in ("t00", "o00") or not code.isdigit():
            continue

        try:
            # 抓 60 日歷史（用 3mo period 取 SQLite 快取）
            df = fetch_data.get_history(code, period="3mo", otc=otc)
            if df is None or df.empty or len(df) < 30:
                logger.warning(
                    f"[{code}] {label} 資料不足"
                    f"（{len(df) if df is not None else 0} 筆），跳過"
                )
                continue

            # 檢查最新資料是否在最近 2 天內（避免用到非交易日資料）
            last_date = df.index[-1]
            if isinstance(last_date, datetime):
                last_date = last_date.date()
            elif hasattr(last_date, 'date'):
                last_date = last_date.date()
            if (today - last_date).days > 2:
                logger.warning(f"[{code}] {label} 資料過舊（{last_date}），跳過")
                continue

            signals = check_signals(df)
            if signals:
                triggered.append({
                    "code": code,
                    "label": label,
                    "signals": signals
                })
                logger.info(f"[{code}] {label} 觸發：{', '.join(signals)}")

            # 避免 yfinance 速率限制
            time.sleep(0.5)

        except Exception as e:
            logger.error(f"[{code}] {label} 處理失敗：{e}")
            continue

    return triggered


def format_message(triggered: list[dict]) -> str:
    """格式化推播訊息。"""
    if not triggered:
        return ""

    today = date.today().strftime("%Y-%m-%d")
    lines = [f"🔔 訊號牆 {today}"]

    for item in triggered:
        code = item["code"]
        label = item["label"]
        signals_str = " + ".join(item["signals"])
        lines.append(f"{code} {label}：{signals_str}")

    return "\n".join(lines)


def main():
    logger.info("=== 訊號牆掃描開始 ===")

    if not ALERT_ENABLED:
        logger.info("ALERT_ENABLED=false，跳過")
        return

    triggered = scan_watchlist()

    if not triggered:
        logger.info("無觸發訊號")
        return

    message = format_message(triggered)
    logger.info(f"推播訊息：\n{message}")

    if send_telegram(message):
        logger.info("Telegram 推播成功")
    else:
        logger.error("Telegram 推播失敗")


if __name__ == "__main__":
    main()
