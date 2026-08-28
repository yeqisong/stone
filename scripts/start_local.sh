#!/bin/bash
# WSL 启动自启（crontab @reboot）：后端 + 前端
# 幂等：已在运行则跳过；日志落 /tmp/stone_local.log
LOG=/tmp/stone_local.log
echo "$(date '+%F %T') ====== stone 自启 ======" >> "$LOG"

if ! curl -s -o /dev/null http://127.0.0.1:8000/health; then
  cd /home/bnbnyu/projects/stone
  setsid nohup venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 >> "$LOG" 2>&1 < /dev/null &
  echo "backend started" >> "$LOG"
else
  echo "backend already up" >> "$LOG"
fi

if ! curl -s -o /dev/null http://127.0.0.1:3000; then
  cd /home/bnbnyu/projects/stone/web-v2
  setsid nohup /usr/bin/node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 3000 >> "$LOG" 2>&1 < /dev/null &
  echo "frontend started" >> "$LOG"
else
  echo "frontend already up" >> "$LOG"
fi
