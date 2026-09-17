# 只給 alert_monitor.py(背景急殺警示推播)用的最小image——不是整個Streamlit App的
# Dockerfile(App本身部署在Streamlit Community Cloud,不透過這個檔案)。alert_monitor.py
# 只用得到requests這個第三方套件,不需要yfinance/pandas/streamlit那些重量級依賴,image
# 越小,fly.io的建置/部署時間跟資源用量也越省。
FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir requests

COPY alert_monitor.py intraday.py reversal_alert.py alert_config_cloud.py ./

CMD ["python", "-u", "alert_monitor.py"]
