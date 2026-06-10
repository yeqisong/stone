#!/bin/bash
# health_check.sh — 每小时健康检查
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

echo "$(date): 健康检查..." >> "$LOG_DIR/health.log"

# 1. FastAPI 健康端点
if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    echo "  FastAPI: OK" >> "$LOG_DIR/health.log"
else
    echo "  FastAPI: FAILED" >> "$LOG_DIR/health.log"
fi

# 2. 磁盘使用率
DISK_USAGE=$(df / | tail -1 | awk '{print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -gt 80 ]; then
    echo "  [WARN] 磁盘使用率: ${DISK_USAGE}%" >> "$LOG_DIR/health.log"
fi

echo "$(date): 健康检查完成" >> "$LOG_DIR/health.log"
