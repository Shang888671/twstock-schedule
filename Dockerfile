# 完整版 Dockerfile — alert_monitor + daily_update + 所有依賴

FROM python:3.13-slim

WORKDIR /app

# 安裝所有依賴（yfinance、pandas、requests）
RUN pip install --no-cache-dir requests yfinance pandas

# 安裝 supercronic（fly.io 用的 cron 引擎）
RUN apt-get update && apt-get install -y curl \
    && curl -fsSL -o /usr/local/bin/supercronic https://github.com/aptible/supercronic/releases/download/v0.2.33/supercronic-linux-amd64 \
    && chmod +x /usr/local/bin/supercronic \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 複製所有 Python 模組
COPY alert_monitor.py intraday.py reversal_alert.py alert_config_cloud.py ./
COPY chip_data.py fetch_data.py margin_data.py database.py risk.py daily_update.py morning_brief.py signals_wall.py ./
COPY crontab start.sh ./

RUN chmod +x start.sh

CMD ["./start.sh"]
