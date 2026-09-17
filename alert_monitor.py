"""盤中急殺/反轉警示——背景輪詢+主動推播(第2步),解決 reversal_alert.py(第1步)
只有開著 Streamlit 網頁分頁才看得到警示的限制。這支腳本獨立於 app.py 之外執行,在自己
電腦上跑(不是雲端App——Streamlit Cloud 沒有背景常駐執行的能力),用 while 迴圈輪詢
TWSE MIS,偵測到警示升級時透過 Telegram Bot 主動推播到手機。

開發起因:使用者親身經歷櫃買指數盤中先漲高、後來忽然急跌,因為沒開著網頁盯盤而錯過,
造成虧損。跟第1步(app.py 裡的即時畫面警示)分開做,是因為「看得到」跟「通知得到」是
兩個完全不同層次的問題,第1步先驗證判斷邏輯準不準,這一步才解決「沒在看畫面」的根本限制。

跟 app.py 共用 intraday.py(抓報價)跟 reversal_alert.py(判斷邏輯),不重複寫一份——
這支腳本只負責「用迴圈代替使用者盯著瀏覽器分頁」跟「推播」這兩件事。

使用方式:
1. 複製 alert_config.example.py 成 alert_config.py,填入你的 Telegram Bot Token/
   Chat ID(取得方式見該檔案內的說明),還有想監控的標的清單。
2. 執行 `python alert_monitor.py`,讓終端機視窗開著跑(要離開電腦時也可以留著,只要
   電腦不關機/不休眠;真的要做到電腦關著也能跑,得另外部署到一台24小時開著的機器,
   這次先不做到那麼遠)。Ctrl+C 結束。
"""

import time
from collections import deque
from datetime import datetime

import requests

import intraday
import reversal_alert

try:
    import alert_config
except ImportError:
    raise SystemExit(
        "找不到 alert_config.py——複製 alert_config.example.py 成 alert_config.py,"
        "填入你的 Telegram Bot Token/Chat ID 跟想監控的標的清單後再執行一次。"
    )

POLL_INTERVAL_SECONDS = 15  # 交易時間內多久打一次API——比app.py的10秒稍寬鬆,背景長時間
                            # 跑要對TWSE MIS客氣一點,不用跟畫面互動所以差5秒感受不到差異。
CLOSED_MARKET_CHECK_SECONDS = 300  # 非交易時間多久檢查一次「開盤了沒」,不用一直打
HISTORY_MAXLEN = 20  # 20筆*15秒=300秒,跟 reversal_alert.FAST_DROP_WINDOW_SECONDS 對齊
RENOTIFY_COOLDOWN_SECONDS = 300  # 同一個標的維持在同一個警示等級時,最多幾秒才重複提醒
                                 # 一次,避免「急殺」持續好幾分鐘時每15秒轟炸一次手機


def _now_str() -> str:
    return datetime.now().strftime("%H:%M:%S")


def send_telegram_message(text: str) -> None:
    url = f"https://api.telegram.org/bot{alert_config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": alert_config.TELEGRAM_CHAT_ID, "text": text}, timeout=10)
        if resp.status_code != 200:
            print(f"[警告] Telegram推播失敗:{resp.status_code} {resp.text}")
    except Exception as e:
        print(f"[警告] Telegram推播失敗:{e}")


class _WatchState:
    """每個監控標的自己的價格歷史緩衝區,跟「上次推播時的警示等級/時間」——避免同一個
    等級每15秒就轟炸使用者手機一次,只在「升級」或「冷卻時間到了還沒恢復正常」時才推播。
    """

    def __init__(self):
        self.price_history = deque(maxlen=HISTORY_MAXLEN)
        self.last_notified_severity = "正常"
        self.last_notified_at = 0.0


def _check_one(target: dict, state: _WatchState) -> None:
    label, mis_code, mis_otc = target["label"], target["code"], target["otc"]
    iq = intraday.get_intraday_quote(mis_code, mis_otc)
    if iq is None or iq["last_price"] is None:
        print(f"[{_now_str()}] {label}: 資料暫時無法取得")
        return

    now = time.time()
    state.price_history.append((now, iq["last_price"]))
    signal = reversal_alert.compute_reversal_signal(iq.get("day_high"), iq["last_price"], list(state.price_history))
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
        # 從警示狀態恢復正常,也推播一次讓使用者安心,不用一直開著終端機確認狀態
        send_telegram_message(f"✅ {label} 已恢復正常\n現價 {iq['last_price']:,.2f}")
        state.last_notified_severity = "正常"
        state.last_notified_at = now


def main() -> None:
    states = {t["label"]: _WatchState() for t in alert_config.WATCH_LIST}
    labels = "、".join(t["label"] for t in alert_config.WATCH_LIST)
    print(f"開始監控:{labels}(Ctrl+C 結束)")
    send_telegram_message(f"🟢 盤中急殺警示監控已啟動,監控標的:{labels}")

    try:
        while True:
            if intraday.is_market_open_now():
                for target in alert_config.WATCH_LIST:
                    _check_one(target, states[target["label"]])
                time.sleep(POLL_INTERVAL_SECONDS)
            else:
                print(f"[{_now_str()}] 非交易時段,{CLOSED_MARKET_CHECK_SECONDS}秒後再檢查")
                time.sleep(CLOSED_MARKET_CHECK_SECONDS)
    except KeyboardInterrupt:
        print("\n已停止監控。")


if __name__ == "__main__":
    main()
