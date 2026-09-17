"""alert_monitor.py 的設定檔範本——複製這個檔案成 alert_config.py(不要直接改這個檔案),
填入你自己的 Telegram Bot Token/Chat ID 跟想監控的標的清單。alert_config.py 已經被
.gitignore 排除,不會被提交到 GitHub,你的 Token 不會外流。

=== 取得 Telegram Bot Token ===
1. 在 Telegram 搜尋「BotFather」這個官方帳號,開始對話。
2. 輸入 /newbot,依照指示幫你的 bot 取名字(隨便取,例如「我的股票警示機器人」)。
3. BotFather 會回傳一組 Token,長得像「123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ」,複製下來。

=== 取得你自己的 Chat ID ===
1. 在 Telegram 找到你剛建立的 bot,隨便傳一句話給它(例如「hi」)——一定要先傳過一句話,
   bot 才「認識」你,之後才能主動推播給你。
2. 用瀏覽器打開這個網址(把 <TOKEN> 換成你上面拿到的 Token):
   https://api.telegram.org/bot<TOKEN>/getUpdates
3. 頁面會顯示一段 JSON,裡面找 "chat":{"id": 數字, ...},這個數字就是你的 Chat ID。
"""

TELEGRAM_BOT_TOKEN = "在這裡貼上你的Bot Token"
TELEGRAM_CHAT_ID = "在這裡貼上你的Chat ID"

# 盤中警示開啟/關閉的總開關——改成 False 存檔後,alert_monitor.py(如果已經在背景執行)
# 最多等 PAUSED_CHECK_SECONDS(30秒)就會自動偵測到並暫停,不用重開腳本;改回 True
# 也一樣自動恢復。想臨時關掉推播(例如開會、不想被打擾)又不想把WATCH_LIST清單刪掉重打時用。
ALERT_ENABLED = True

# 想監控的標的清單,otc=False是上市股票/加權指數,otc=True是上櫃股票/櫃買指數。
# code 是股票代號,或加權指數用固定代號"t00"、櫃買指數用固定代號"o00"(這兩個是TWSE MIS
# 對指數的代號,不是股票代號,細節見 app.py 裡「mis_code」相關的說明)。
#
# 每個標的可以選擇性加 pullback_warn_pct/pullback_severe_pct/fast_drop_severe_pct 這三個
# 欄位,覆蓋 reversal_alert.py 裡的全域預設門檻(1%/2%/0.8%)——沒加這幾個欄位就是用預設值,
# 加權指數/櫃買指數這種波動平穩的標的通常不用特別設定。波動大的個股(例如小型股、興櫃股)
# 建議設寬鬆一點的門檻,不然正常的盤中震盪就會一直誤報。
WATCH_LIST = [
    {"label": "櫃買指數", "code": "o00", "otc": True},
    {"label": "加權指數", "code": "t00", "otc": False},
    # {"label": "台積電", "code": "2330", "otc": False},  # 大型權值股,波動溫和,用預設門檻即可
    # {"label": "高波動小型股", "code": "1234", "otc": False,
    #  "pullback_warn_pct": 3.0, "pullback_severe_pct": 5.0, "fast_drop_severe_pct": 1.5},
]
