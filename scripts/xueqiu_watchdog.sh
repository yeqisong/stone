#!/bin/bash
# 雪球 poller 看门狗（cron 每 10 分钟）：进程不在 → 拉起；心跳停 > 35 分钟 → 杀掉重启。
# 心跳正常间隔 ≤5.5 分钟（休眠分支 5min 醒一次也跳）、合法封禁冷却 ≤10.2 分钟
# （冷却期已改为保心跳）——>35 分钟即挂死/僵尸，重启是唯一自愈手段。
# 2026-10 两次事故：poller 崩溃/僵尸后 4 天无人知晓，数据断采。
ROOT=/home/bnbnyu/projects/stone
LOG=$ROOT/logs/xueqiu_watchdog.log
PATTERN='[v]env/bin/python -m scripts.xueqiu_poller'   # [v] 防自匹配
ts() { date '+%F %T'; }

if pgrep -f "$PATTERN" >/dev/null; then
  age=$("$ROOT/venv/bin/python" - "$ROOT" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from app.db.connection import get_sync_db
from sqlalchemy import text
db = get_sync_db()
try:
    r = db.execute(text("SELECT EXTRACT(EPOCH FROM now() - heartbeat_at) FROM xueqiu_runtime WHERE id=1")).scalar()
    print(int(float(r)) if r is not None else 999999)
except Exception:
    print(999999)
finally:
    db.close()
PY
)
  if [ "$age" -gt 2100 ]; then
    echo "$(ts) 心跳已停 ${age}s（>35min），kill 并重启 poller" >> "$LOG"
    pkill -9 -f "$PATTERN"
    sleep 1
  else
    exit 0   # 活着且心跳正常
  fi
fi

cd "$ROOT" || exit 1
mkdir -p /tmp/xq_p0 logs
setsid nohup "$ROOT/venv/bin/python" -m scripts.xueqiu_poller >> "$ROOT/logs/xueqiu_poller.log" 2>&1 < /dev/null &
sleep 2
pid=$(pgrep -f "$PATTERN" | head -1)
echo "$(ts) watchdog 拉起 poller (pid ${pid:-unknown})" >> "$LOG"
