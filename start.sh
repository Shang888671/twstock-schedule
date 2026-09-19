#!/bin/bash
# 同時啟動 alert_monitor + supercronic（在背景跑 alert_monitor，前景跑 cron）

echo "[start] 啟動 alert_monitor（背景）..."
python -u alert_monitor.py &

echo "[start] 啟動 supercronic（前景）..."
exec supercronic /app/crontab
