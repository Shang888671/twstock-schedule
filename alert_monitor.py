"""盤中急殺/反轉警示——背景輪詢+主動推播（SQLite 優化版）。

改善項目：
1. 共用 database.py 的 SQLite 快取（alert_monitor → database.upsert_mis_quote）
2. Telegram 推播改用 requests.Session() 復用連線 + 指數退避重試
3. alert_config.reload() 改為僅在檔案 mtime 變化時重載（避免每 15 秒 reload 模組）
4. 加入更完善的錯誤處理（batch query 失敗不中斷整個 loop）
"""

import importlib
import os
import time
from collections import deque
from datetime import datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import intraday
import reversal_alert
from database import upsert_mis_quote

try:
    import alert_config
except ImportError:
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        import alert_config_cloud as alert_config
    else:
        raise SystemExit(
            "找不到 alert_config.py——複製 alert_config.example.py 成 alert_config.py,"
            "填入你的 Telegram Bot Token/Chat ID 跟想監控的標的清單後再執行一次。"
        )

POLL_INTERVAL_SECONDS = 15
CLOSED_MARKET_CHECK_SECONDS = 300
HISTORY_MAXLEN = 20
PAUSED_CHECK_SECONDS = 30
RENOTIFY_COOLDOWN_SECONDS = 300

# Telegram session（復用連線）
_tg_session: requests.Session | None = None


def _get_tg_session() -> requests.Session:
    global _tg_session
    if _tg_session is None:
        _tg_session = requests.Session()
        retry = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        _tg_session.mount("https://", adapter)
    return _tg_session


def _now_str() -> str:
    return datetime.now().strftime("%H:%M:%S")


def send_telegram_message(text: str) -> None:
    url = f"https://api.telegram.org/bot{alert_config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        session = _get_tg_session()
        resp = session.post(url, json={"chat_id": alert_config.TELEGRAM_CHAT_ID, "text": text}, timeout=10)
        if resp.status_code != 200:
            print(f"[警告] Telegram推播失敗:{resp.status_code} {resp.text}")
    except Exception as e:
        print(f"[警告] Telegram推播失敗:{e}")


class _WatchState:
    def __init__(self):
        self.price_history = deque(maxlen=HISTORY_MAXLEN)
        self.last_notified_severity = "正常"
        self.last_notified_at = 0.0


def _check_one(target: dict, iq: dict | None, state: _WatchState) -> None:
    label = target["label"]
    if iq is None or iq["last_price"] is None:
        print(f"[{_now_str()}] {label}: 資料暫時無法取得")
        return

    now = time.time()
    state.price_history.append((now, iq["last_price"]))
    signal = reversal_alert.compute_reversal_signal(
        iq.get("day_high"),
        iq["last_price"],
        list(state.price_history),
        pullback_warn_pct=target.get("pullback_warn_pct"),
        pullback_severe_pct=target.get("pullback_severe_pct"),
        fast_drop_severe_pct=target.get("fast_drop_severe_pct"),
    )
    severity = signal["severity"]
    print(f"[{_now_str()}] {label}: {iq['last_price']:,.2f} | {severity} | {signal['detail']}")

    severity_escalated = (
        reversal_alert.SEVERITY_ORDER[severity] > reversal_alert.SEVERITY_ORDER[state.last_notified_severity]
    )
    cooldown_expired = (now - state.last_notified_at) >= RENOTIFY_COOLDOWN_SECONDS

    if severity != "正常" and (severity_escalated or cooldown_expired):
        emoji = {"急殺": "🔻", "拉回": "⚠️"}.get(severity, "")
        send_telegram_message(f"{emoji} {label} {severity}\n現價 {iq['last_price']:,.2f}\n{signal['detail']}")
        state.last_notified_severity = severity
        state.last_notified_at = now
    elif severity == "正常" and state.last_notified_severity != "正常":
        send_telegram_message(f"✅ {label} 已恢復正常\n現價 {iq['last_price']:,.2f}")
        state.last_notified_severity = "正常"
        state.last_notified_at = now


def main() -> None:
    states = {(t["code"], t["otc"]): _WatchState() for t in alert_config.WATCH_LIST}
    label_list = [t["label"] for t in alert_config.WATCH_LIST]
    if len(label_list) <= 10:
        labels_display = "、".join(label_list)
    else:
        labels_display = "、".join(label_list[:8]) + f" 等共{len(label_list)}檔"
    print(f"開始監控:{labels_display}(Ctrl+C 結束)")
    send_telegram_message(f"🟢 盤中急殺警示監控已啟動,監控標的:{labels_display}")

    last_enabled_state = True
    last_daily_ping_date = None
    _config_mtime = os.path.getmtime("alert_config.py") if os.path.exists("alert_config.py") else 0

    try:
        while True:
            # 只在 alert_config.py 檔案 mtime 變化時重載（而非每輪 reload）
            try:
                current_mtime = os.path.getmtime("alert_config.py")
                if current_mtime != _config_mtime:
                    importlib.reload(alert_config)
                    _config_mtime = current_mtime
                    print(f"[{_now_str()}] alert_config.py 已更新，重新載入")
            except Exception:
                pass

            alert_enabled = getattr(alert_config, "ALERT_ENABLED", True)

            if alert_enabled != last_enabled_state:
                if alert_enabled:
                    send_telegram_message("🟢 盤中急殺警示監控已恢復")
                else:
                    send_telegram_message("🟡 盤中急殺警示監控已暫停(alert_config.py的ALERT_ENABLED=False)")
                last_enabled_state = alert_enabled

            if not alert_enabled:
                print(f"[{_now_str()}] 監控已暫停,{PAUSED_CHECK_SECONDS}秒後重新檢查設定")
                time.sleep(PAUSED_CHECK_SECONDS)
                continue

            if intraday.is_market_open_now():
                try:
                    quotes = intraday.get_intraday_quotes_batch(
                        [(t["code"], t["otc"]) for t in alert_config.WATCH_LIST]
                    )
                    # 快取到 SQLite
                    for (code, otc), quote in quotes.items():
                        if quote and quote.get("last_price"):
                            upsert_mis_quote(code, quote)
                except Exception as e:
                    print(f"[{_now_str()}] 批次查詢失敗：{e}")
                    quotes = {}

                today = datetime.now().date()
                if quotes and today != last_daily_ping_date:
                    send_telegram_message(
                        f"☀️ {_now_str()} 開盤監控運作中,監控{len(alert_config.WATCH_LIST)}檔・"
                        f"本輪成功查到{len(quotes)}檔報價"
                    )
                    last_daily_ping_date = today

                for target in alert_config.WATCH_LIST:
                    key = (target["code"], target["otc"])
                    _check_one(target, quotes.get(key), states[key])
                time.sleep(POLL_INTERVAL_SECONDS)
            else:
                print(f"[{_now_str()}] 非交易時段,{CLOSED_MARKET_CHECK_SECONDS}秒後再檢查")
                time.sleep(CLOSED_MARKET_CHECK_SECONDS)
    except KeyboardInterrupt:
        print("\n已停止監控。")


if __name__ == "__main__":
    main()
