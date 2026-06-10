#!/bin/bash
# 历史数据初始化脚本
# 用法: bash scripts/init_data.sh [--days 365]
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

DAYS=1825  # 默认5年
EXTRA_ARGS=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --days) DAYS="$2"; shift 2 ;;
        --start) START_DATE="$2"; shift 2 ;;
        --end) END_DATE="$2"; shift 2 ;;
        *) echo "Unknown: $1"; exit 1 ;;
    esac
done

echo "============================================"
echo "  个股买卖点监测系统 - 历史数据初始化"
echo "============================================"
echo ""

# 1. 检查数据库连接
echo "[1/3] 检查数据库连接..."
python3 -c "
import sys; sys.path.insert(0, '.')
from app.db.connection import get_sync_db
from sqlalchemy import text
db = get_sync_db()
db.execute(text('SELECT 1'))
print('  数据库连接正常')
db.close()
"

# 2. 下载K线数据
echo "[2/3] 下载历史K线数据..."
if [ -n "$START_DATE" ] && [ -n "$END_DATE" ]; then
    echo "  日期范围: $START_DATE ~ $END_DATE"
    python3 scripts/resume_kline.py --start "$START_DATE" --end "$END_DATE"
elif [ "$DAYS" -lt 1825 ]; then
    START_DATE=$(python3 -c "from datetime import date,timedelta; print((date.today()-timedelta(days=$DAYS)).isoformat())")
    END_DATE=$(date +%Y-%m-%d)
    echo "  日期范围: $START_DATE ~ $END_DATE (最近$DAYS天)"
    python3 scripts/resume_kline.py --start "$START_DATE" --end "$END_DATE"
else
    echo "  全量下载 (2021-01-01 ~ 今天)"
    python3 scripts/resume_kline.py
fi

# 3. 下载基本面
echo "[3/3] 下载基本面数据..."
python3 -c "
import sys; sys.path.insert(0, '.')
from crawler.baostock_crawler import BaostockCrawler
c = BaostockCrawler()
c.download_fundamentals(skip_existing=True)
c.logout()
print('  基本面下载完成')
"

echo ""
echo "============================================"
echo "  数据初始化完成！"
echo "============================================"
