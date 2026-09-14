#!/usr/bin/env bash
# 数据库每日全量备份 → 七牛云 Kodo（restic 增量去重上传）
#
# 原理：pg_dump -Fc 全量逻辑备份（MVCC 一致性快照，不停库不阻塞读），
# restic 按"内容定义分块"去重上传——首次全量上传，之后每天只传变化的数据块；
# 保留最近 30 个每日还原点，每周日自动清理过期块。不需要"删昨天、传今天"。
#
# 一次性配置：
#   1. 创建 ~/.config/stone-backup/qiniu.env（chmod 600），内容：
#        RESTIC_REPOSITORY=s3:https://s3.cn-east-1.qiniucs.com/<bucket名>
#        AWS_ACCESS_KEY_ID=<七牛 AK>
#        AWS_SECRET_ACCESS_KEY=<七牛 SK>
#        RESTIC_PASSWORD=<备份库加密口令，丢失则备份不可恢复>
#      端点按 bucket 所在区域选：s3.cn-east-1(华东浙江) / s3.cn-east-2(浙江2)
#      / s3.cn-north-1(华北) / s3.cn-south-1(华南) / s3.ap-southeast-2(东南亚)
#   2. 初始化备份仓库（仅一次）： scripts/backup_qiniu.sh init
#
# 定时：crontab 已挂每天 01:30（北京时间，避开 00:05 夜间补数）。
set -euo pipefail

ENV_FILE="$HOME/.config/stone-backup/qiniu.env"
RESTIC="$HOME/bin/restic"
DUMP_DIR="${DUMP_DIR:-/home/bnbnyu/projects/stone/data/backups/db}"   # 本地暂存放 WSL ext4（9p 写 /mnt/c 多一层缓存易在内存吃紧时 ENOMEM，且更慢）
CONTAINER=stock-db
PG_USER=stock
PG_DB=stock_monitor
KEEP_DAILY=30

log() { echo "[backup_qiniu $(date '+%F %T')] $*"; }

if [ ! -f "$ENV_FILE" ]; then
    log "跳过：$ENV_FILE 不存在（尚未配置七牛凭据）"
    exit 0
fi
# set -a：source 进来的变量自动 export，restic 子进程才能读到
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

case "${1:-backup}" in
  init)
    mkdir -p "$DUMP_DIR"
    "$RESTIC" init
    exit 0
    ;;
  backup)
    ;;
  *)
    echo "用法: $0 [backup|init]" >&2
    exit 1
    ;;
esac

mkdir -p "$DUMP_DIR"
exec 9>"$DUMP_DIR/.lock"
flock -n 9 || { log "上一次备份仍在运行，跳过本次"; exit 0; }

# 剩余空间需容纳新旧两份 dump（经验值：-Fc 压缩后约为库体积的 1/4~1/6）
AVAIL_GB=$(df -BG --output=avail "$DUMP_DIR" | tail -1 | tr -dc '0-9')
if [ "$AVAIL_GB" -lt 120 ]; then
    log "中止：$DUMP_DIR 剩余 ${AVAIL_GB}GB 不足 120GB"
    exit 1
fi

DUMP="$DUMP_DIR/stock_monitor_latest.dump"
TMP="$DUMP.part"
trap 'rm -f "$TMP"' EXIT

log "开始 pg_dump 全量导出（Fc 压缩，不停库）..."
docker exec "$CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" -Fc > "$TMP"
mv -f "$TMP" "$DUMP"
trap - EXIT
log "导出完成（$(du -h "$DUMP" | cut -f1)），开始 restic 上传（自动只传变化块）..."

"$RESTIC" backup "$DUMP" --tag db-nightly

# 每周日执行保留策略：保留最近 30 个每日快照，清理过期数据块
if [ "$(date +%u)" = "7" ]; then
    log "周日：执行 forget --prune（保留 ${KEEP_DAILY} 天）..."
    "$RESTIC" forget --keep-daily "$KEEP_DAILY" --prune
fi

log "完成。最新快照："
"$RESTIC" snapshots --latest 1
