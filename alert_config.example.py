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

# 想監控的標的清單,otc=False是上市股票/加權指數,otc=True是上櫃股票/櫃買指數。
# code 是股票代號,或加權指數用固定代號"t00"、櫃買指數用固定代號"o00"(這兩個是TWSE MIS
# 對指數的代號,不是股票代號,細節見 app.py 裡「mis_code」相關的說明)。
WATCH_LIST = [
    {"label": "櫃買指數", "code": "o00", "otc": True},
    {"label": "加權指數", "code": "t00", "otc": False},
    # {"label": "台積電", "code": "2330", "otc": False},
]
