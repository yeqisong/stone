#!/bin/bash
# daily_crawl.sh — 每日数据采集与策略计算
# 由 cron 在每交易日 17:35 触发
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

CRAWL_LOG="$LOG_DIR/crawl.log"
STRATEGY_LOG="$LOG_DIR/strategy.log"

MAX_RETRIES=3
RETRY_DELAYS=(1800 1800 3600)  # 30min, 30min, 1h

echo "$(date '+%Y-%m-%d %H:%M:%S'): ====== 每日数据采集开始 ======" >> "$CRAWL_LOG"

# ── 1. 交易日检查 ──
echo "$(date '+%Y-%m-%d %H:%M:%S'): [1/4] 交易日检查..." >> "$CRAWL_LOG"
cd "$SCRIPT_DIR"
python3 -c "
import sys
sys.path.insert(0, '.')
from app.db.connection import get_sync_db
from crawler.trade_calendar import is_trade_day
db = get_sync_db()
try:
    ok = is_trade_day(db_session=db)
    sys.exit(0 if ok else 1)
finally:
    db.close()
"
if [ $? -ne 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S'): 非交易日，跳过采集" >> "$CRAWL_LOG"
    exit 0
fi

# ── 2. 补采缺失交易日 ──
echo "$(date '+%Y-%m-%d %H:%M:%S'): [2/4] 检查并补采缺失数据..." >> "$CRAWL_LOG"
python3 -c "
import sys
sys.path.insert(0, '.')
from crawler.catch_up import catch_up
result = catch_up(max_days=10)
if result.get('failed_dates'):
    print(f'[WARN] 补采失败日期: {result[\"failed_dates\"]}')
print(f'补采完成: 缺失{result[\"missing_count\"]}天, 成功{result[\"caught_up\"]}天')
" >> "$CRAWL_LOG" 2>&1

# ── 3. 采集当日数据（含重试） ──
echo "$(date '+%Y-%m-%d %H:%M:%S'): [3/4] 采集当日数据..." >> "$CRAWL_LOG"

download_success=false
for i in $(seq 0 $MAX_RETRIES); do
    echo "$(date '+%Y-%m-%d %H:%M:%S'):   尝试 $((i+1))/$((MAX_RETRIES+1))..." >> "$CRAWL_LOG"

    python3 -c "
import sys
sys.path.insert(0, '.')
from datetime import date
from crawler.baostock_crawler import BaostockCrawler

crawler = BaostockCrawler()
try:
    result = crawler.download_daily_update(date.today())
    if result.get('fatal'):
        print(f'FATAL: {result[\"fatal\"]}')
        sys.exit(2)
    print(f'完成: +{result[\"rows\"]}行, {result[\"stocks\"]}只有数据, '
          f'{result[\"errors\"]}错误, 跳过{result[\"skipped\"]}只, '
          f'耗时{result[\"elapsed_seconds\"]:.0f}秒')
    if result.get('failed_codes'):
        print(f'失败代码: {result[\"failed_codes\"][:30]}...')
    sys.exit(0 if result['errors'] == 0 else 1)
finally:
    crawler.logout()
" >> "$CRAWL_LOG" 2>&1

    exit_code=$?
    if [ $exit_code -eq 0 ]; then
        download_success=true
        echo "$(date '+%Y-%m-%d %H:%M:%S'):   采集成功" >> "$CRAWL_LOG"
        break
    elif [ $exit_code -eq 2 ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S'):   致命错误，不重试" >> "$CRAWL_LOG"
        break
    fi

    if [ $i -lt $MAX_RETRIES ]; then
        DELAY=${RETRY_DELAYS[$i]}
        echo "$(date '+%Y-%m-%d %H:%M:%S'):   采集失败，${DELAY}s后重试" >> "$CRAWL_LOG"
        sleep $DELAY
    fi
done

# ── 4. DAG 流水线（自动触发 model_signal + model_health）──
if $download_success; then
    echo "$(date '+%Y-%m-%d %H:%M:%S'): [4/4] DAG 流水线..." >> "$CRAWL_LOG"
    python3 -c "import sys; sys.path.insert(0,'.'); from scripts.pipeline import dag; dag.run('daily_update')" >> "$STRATEGY_LOG" 2>&1
    echo "$(date '+%Y-%m-%d %H:%M:%S'):   DAG 完成" >> "$CRAWL_LOG"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S'): [4/4] 采集失败，跳过" >> "$CRAWL_LOG"
fi

# ── 5. 完成 ──
if $download_success; then
    echo "$(date '+%Y-%m-%d %H:%M:%S'): ====== 每日流程完成 ======" >> "$CRAWL_LOG"
    exit 0
else
    echo "$(date '+%Y-%m-%d %H:%M:%S'): [ALERT] 采集彻底失败，已触发告警" >> "$CRAWL_LOG"
    # TODO: 飞书告警 webhook
    exit 1
fi
