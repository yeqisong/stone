#!/bin/bash
# WSL 启动自启（crontab @reboot）：后端 + 前端
# 幂等：已在运行则跳过；日志落 /tmp/stone_local.log
LOG=/tmp/stone_local.log
echo "$(date '+%F %T') ====== stone 自启 ======" >> "$LOG"

if ! curl -s -o /dev/null http://127.0.0.1:8000/health; then
  cd /home/bnbnyu/projects/stone
  # 等数据库就绪（docker 容器随 WSL 启动晚于 cron @reboot，直接拉后端会因 init_db 失败退出）
  for i in $(seq 1 60); do
    docker exec stock-db pg_isready -U stock -d stock_monitor >/dev/null 2>&1 && break
    sleep 2
  done
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

# feature_values 二级索引（瘦身重建时推迟到迁 D 后构建；IF NOT EXISTS 幂等，首启约 2-4 分钟）
docker exec stock-db psql -U stock -d stock_monitor -c "SET maintenance_work_mem='2GB'; CREATE INDEX IF NOT EXISTS idx_fv_feature_date ON feature_values (feature_name, trade_date DESC, stock_code);" >> "$LOG" 2>&1

# Alpha158 全量断点续跑（幂等：进度文件记录已完成项，进程已在跑则跳过）
if ! pgrep -f 'alpha158_full' >/dev/null; then
  cd /home/bnbnyu/projects/stone
  setsid nohup venv/bin/python scripts/alpha158_full.py --start-year 2017 >> /tmp/a158_full.log 2>&1 < /dev/null &
  echo "a158 resume started (2017+)" >> "$LOG"
fi

# 资金流历史回补自动续跑（幂等：跳过已入库日期；配额熔断自动跨天续）
if ! pgrep -f 'backfill_moneyflow' >/dev/null; then
  cd /home/bnbnyu/projects/stone
  setsid nohup venv/bin/python scripts/backfill_moneyflow.py >> /tmp/backfill_mf.log 2>&1 < /dev/null &
  echo "moneyflow backfill resumed" >> "$LOG"
fi
