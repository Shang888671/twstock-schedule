"""alert_monitor.py 在雲端主機(例如 fly.io)上執行時使用的設定來源——跟 alert_config.py
接口完全一樣(同樣的變數名),差別只在密鑰不是寫死在檔案裡,而是從環境變數讀。這個檔案
本身不含任何密鑰,可以放心進版控。

部署到 fly.io 時,用 `fly secrets set TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...` 設定
這兩個環境變數(fly secrets 不會出現在程式碼或 log 裡)。WATCH_LIST 不是密鑰,直接寫死
在這裡就好,要改監控標的就直接改這個檔案、`fly deploy` 重新部署即可。

想暫停/恢復推播:改用 `fly secrets set ALERT_ENABLED=false` 再 `fly deploy`(雲端主機
沒辦法像本機那樣直接編輯檔案讓程式馬上偵測到,一定要重新部署才會生效——這跟本機執行
alert_monitor.py 時,改 alert_config.py 就能在30秒內自動生效的行為不一樣)。
"""

import os

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ALERT_ENABLED = os.environ.get("ALERT_ENABLED", "true").lower() != "false"

WATCH_LIST = [
    {"label": "櫃買指數", "code": "o00", "otc": True},
    {"label": "加權指數", "code": "t00", "otc": False},
]
