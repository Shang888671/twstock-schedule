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

想暫時關掉推播(不想整支腳本停掉重開)時,把 alert_config.py 的 ALERT_ENABLED 改成
False 存檔就好——腳本會在下一輪(最多 PAUSED_CHECK_SECONDS 秒後)自動偵測到並暫停,
改回 True 也會自動恢復,不用重新執行這支腳本。

**每日開盤健康回報**:每個交易日開盤後第一輪批次查詢成功就會推播一次「開盤監控運作中」
訊息(附監控檔數/實際查到檔數),完全獨立於本機或任何App有沒有開著——這支腳本本來就是
24小時跑在雲端(fly.io)上,使用者只要看手機有沒有收到這則訊息,就能確認系統當天正常
運作,不需要另外開任何工具確認。
"""

import importlib
import os
import time
from collections import deque
from datetime import datetime

import requests

import intraday
import reversal_alert

try:
    import alert_config
except ImportError:
    # 本機沒有 alert_config.py(例如在雲端主機上執行,不會把含密鑰的檔案傳上去)——
    # 改用 alert_config_cloud.py,它不含任何密鑰,改成從環境變數讀TELEGRAM_BOT_TOKEN
    # 等設定(部署方式見 fly.toml/Dockerfile)。兩者接口一致(同樣的變數名),main()
    # 之後的邏輯完全不用區分是哪一種來源。
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        import alert_config_cloud as alert_config
    else:
        raise SystemExit(
            "找不到 alert_config.py——複製 alert_config.example.py 成 alert_config.py,"
            "填入你的 Telegram Bot Token/Chat ID 跟想監控的標的清單後再執行一次。"
            "(在雲端主機上執行的話,改用環境變數TELEGRAM_BOT_TOKEN,見alert_config_cloud.py)"
        )

POLL_INTERVAL_SECONDS = 15  # 交易時間內多久打一次API——比app.py的10秒稍寬鬆,背景長時間
                            # 跑要對TWSE MIS客氣一點,不用跟畫面互動所以差5秒感受不到差異。
CLOSED_MARKET_CHECK_SECONDS = 300  # 非交易時間多久檢查一次「開盤了沒」,不用一直打
HISTORY_MAXLEN = 20  # 20筆*15秒=300秒,跟 reversal_alert.FAST_DROP_WINDOW_SECONDS 對齊
PAUSED_CHECK_SECONDS = 30  # ALERT_ENABLED=False(暫停監控)時多久重新檢查一次設定檔,
                           # 比CLOSED_MARKET_CHECK_SECONDS短是因為「暫停」通常是使用者
                           # 臨時想關掉一下,不像非交易時段那樣可以放心等很久。
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


def _check_one(target: dict, iq: dict | None, state: _WatchState) -> None:
    label = target["label"]
    if iq is None or iq["last_price"] is None:
        print(f"[{_now_str()}] {label}: 資料暫時無法取得")
        return

    now = time.time()
    state.price_history.append((now, iq["last_price"]))
    # target 裡的 pullback_warn_pct/pullback_severe_pct/fast_drop_severe_pct 是選填的
    # 每個標的自訂門檻(見 alert_config.example.py),沒填就是 None,compute_reversal_signal
    # 會自動退回全域預設值。
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
        # 從警示狀態恢復正常,也推播一次讓使用者安心,不用一直開著終端機確認狀態
        send_telegram_message(f"✅ {label} 已恢復正常\n現價 {iq['last_price']:,.2f}")
        state.last_notified_severity = "正常"
        state.last_notified_at = now


def main() -> None:
    # state用(code, otc)當key,不用label——247檔規模的監控清單裡不同代號的中文名稱理論上
    # 可能重複(例如簡稱雷同),用label當key有極小機率互相覆蓋掉對方的歷史價格緩衝區,
    # (code, otc)才是真正唯一的識別。
    states = {(t["code"], t["otc"]): _WatchState() for t in alert_config.WATCH_LIST}
    label_list = [t["label"] for t in alert_config.WATCH_LIST]
    # 監控清單一多(例如整份.dsl自選股),Telegram訊息塞滿幾百個名字反而看不清楚,超過10檔
    # 就只顯示前幾檔+總數,不然LINE/Telegram訊息長度也可能被截斷。
    if len(label_list) <= 10:
        labels_display = "、".join(label_list)
    else:
        labels_display = "、".join(label_list[:8]) + f" 等共{len(label_list)}檔"
    print(f"開始監控:{labels_display}(Ctrl+C 結束)")
    send_telegram_message(f"🟢 盤中急殺警示監控已啟動,監控標的:{labels_display}")

    # 記錄「上一輪看到的ALERT_ENABLED」,只在狀態真的改變(開→關、關→開)時才推播通知,
    # 不然每次迴圈都重複發同一句「已暫停」訊息。用 getattr 給預設值True,是因為使用者
    # 可能是從舊版alert_config.py升級上來、還沒加這個欄位,不應該因此直接壞掉。
    last_enabled_state = True

    # 每天開盤後第一次成功查到報價時推播一次「還活著」訊息——起因是使用者要求「不想受
    # Claude Code這個App有沒有開著影響」確認監控正常運作。這支腳本本來就是24小時跑在
    # fly.io上、跟本機/App完全獨立,單靠開頭那則「監控已啟動」訊息只在腳本重啟(部署)時
    # 觸發一次,平常不會再有任何訊息證明「今天早上真的有在跑」,使用者沒辦法只憑手機就
    # 確認系統健康。改成每個交易日開盤後第一輪批次查詢成功就推播一次,附上「監控幾檔／
    # 這輪實際查到幾檔」的數字當健康度證據(查到的檔數明顯偏少代表批次查詢可能有問題),
    # 使用者收到這則訊息本身就是「系統活著」的證明,不用回頭問任何人或開任何App確認。
    last_daily_ping_date = None

    try:
        while True:
            # 每輪都重新載入 alert_config.py,讓使用者存檔修改 ALERT_ENABLED 之後,
            # 不用重開這支腳本、下一輪(最多PAUSED_CHECK_SECONDS或POLL_INTERVAL_SECONDS秒
            # 之後)就會生效——這也是為什麼WATCH_LIST在main()一開始就先讀死了一份,
            # 這裡reload不會讓「監控標的清單」跟著動態變動,只有ALERT_ENABLED這個開關
            # 是刻意設計成可以immediately生效的。
            importlib.reload(alert_config)
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
                # 改用批次查詢(見intraday.get_intraday_quotes_batch的說明)——監控清單
                # 一多(例如整份247檔的.dsl自選股),一檔一檔打API會讓單輪輪詢時間隨監控
                # 檔數線性增加,遠超過POLL_INTERVAL_SECONDS;TWSE MIS這個端點本身就支援
                # 一次查多檔,幾百檔也只要幾次API呼叫就能查完一輪。
                quotes = intraday.get_intraday_quotes_batch(
                    [(t["code"], t["otc"]) for t in alert_config.WATCH_LIST]
                )

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
