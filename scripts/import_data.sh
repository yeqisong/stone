#!/bin/bash
# 数据导入脚本 — 将预打包数据导入 PostgreSQL
# 用法: bash scripts/import_data.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

DUMP_FILE="data/stock_monitor_dump.sql.gz"

echo "============================================"
echo "  数据导入 PostgreSQL"
echo "============================================"

if [ ! -f "$DUMP_FILE" ]; then
    echo "错误: 找不到 $DUMP_FILE"
    echo ""
    echo "请先获取数据包:"
    echo "  scp data/stock_monitor_dump.sql.gz user@server:/opt/stone/data/"
    echo ""
    echo "或从网络下载:"
    echo "  docker compose exec app bash scripts/init_data.sh"
    exit 1
fi

SIZE=$(du -h "$DUMP_FILE" | cut -f1)
echo "数据包: $DUMP_FILE ($SIZE)"
echo "目标: PostgreSQL (db 容器)"
echo "预计耗时: 10-30 分钟"
echo ""

echo "开始导入..."
zcat "$DUMP_FILE" | docker compose exec -T db psql -U stock stock_monitor

echo ""
echo "============================================"
echo "  导入完成！验证数据:"
echo "  docker compose exec db psql -U stock stock_monitor -c \"SELECT COUNT(*) FROM daily_quote\""
echo "============================================"
