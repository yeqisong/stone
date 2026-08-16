#!/bin/bash
# daily_crawl.sh — 每日数据采集（v3.2 架构：触发 dag_flows 流程执行）
# 由 cron 在每交易日 17:35 触发；流程编排见状态页 DAG 管理
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

CRAWL_LOG="$LOG_DIR/crawl.log"
STRATEGY_LOG="$LOG_DIR/strategy.log"

echo "$(date '+%Y-%m-%d %H:%M:%S'): ====== 每日数据采集开始 ======" >> "$CRAWL_LOG"

# ── 1. 交易日检查 ──
echo "$(date '+%Y-%m-%d %H:%M:%S'): [1/3] 交易日检查..." >> "$CRAWL_LOG"
cd "$SCRIPT_DIR"
python3 -c "
import sys
sys.path.insert(0, '.')
from datetime import date
from crawler.trade_calendar import is_trade_day
from app.db.connection import get_sync_db
db = get_sync_db()
try:
    if not is_trade_day(db, date.today()):
        print('非交易日')
        sys.exit(1)
finally:
    db.close()
"
if [ $? -ne 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S'): 非交易日，跳过采集" >> "$CRAWL_LOG"
    exit 0
fi

# ── 2. 缺失交易日检查（缺口提示，补数请在状态页操作）──
echo "$(date '+%Y-%m-%d %H:%M:%S'): [2/3] 缺失交易日检查..." >> "$CRAWL_LOG"
python3 -c "
import sys
sys.path.insert(0, '.')
from datetime import date, timedelta
from crawler.trade_calendar import get_last_trade_date
from app.db.connection import get_sync_db
from sqlalchemy import text
db = get_sync_db()
try:
    last = db.execute(text('SELECT MAX(trade_date) FROM daily_quote')).scalar()
    cal_last = get_last_trade_date(db, date.today())
    if last and cal_last and last < cal_last:
        print(f'[WARN] daily_quote 最新 {last}，距最近交易日 {cal_last} 有缺口，'
              f'请到状态页-历史补数 补数（tushare 配额有限，自动补采已禁用）')
    else:
        print('无缺口')
finally:
    db.close()
" >> "$CRAWL_LOG" 2>&1

# ── 3. 触发 DAG 流程（采集 + 统计 + 信号全链路，编排见 dag_flows）──
echo "$(date '+%Y-%m-%d %H:%M:%S'): [3/3] 触发 DAG 流程..." >> "$CRAWL_LOG"
python3 -c "
import sys
sys.path.insert(0, '.')
from datetime import date
from app.api.dag_flows import _execute_flow_internal
from app.db.connection import get_sync_db
from sqlalchemy import text

db = get_sync_db()
try:
    flow = db.execute(text(\"SELECT id, flow_name FROM dag_flows WHERE status='published' ORDER BY id LIMIT 1\")).fetchone()
finally:
    db.close()
if not flow:
    print('[WARN] 无已发布流程，跳过（请先在 DAG 管理页创建并发布流程）')
    sys.exit(0)
print(f'执行流程: {flow[1]} (id={flow[0]})')
result = _execute_flow_internal(flow[0], {'trade_date': date.today().isoformat()})
if result.get('error'):
    print(f'[ALERT] 流程触发失败: {result[\"error\"]}')
    sys.exit(2)
print(f'已触发 task_id={result.get(\"task_id\")}，进度见前端页面')
" >> "$STRATEGY_LOG" 2>&1

echo "$(date '+%Y-%m-%d %H:%M:%S'): ====== 每日流程完成 ======" >> "$CRAWL_LOG"
exit 0
