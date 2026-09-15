#!/usr/bin/env bash
# 数据库每日全量备份 → 七牛云 Kodo（restic 增量去重上传）
#
# 原理：pg_dump -Fd 目录格式（每表一个独立文件，MVCC 一致性快照，不停库不阻塞读），
# restic 按"内容定义分块"去重上传——没变的表字节级不变，每晚只传变化的表的增量块；
# 保留最近 30 个每日还原点，每周日自动清理过期块。不需要"删昨天、传今天"。
# 注意：-Fc 单文件字节流不稳定（头部时间戳/TOC 偏移导致分块错位），实测两次全量间
# 去重失效；-Fd 逐表文件才让"全量备份+增量上传"真正成立。
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
DUMP_DIR="${DUMP_DIR:-/home/bnbnyu/projects/stone/data/backups/db}"   # WSL ext4（9p 写 /mnt/c 多一层缓存易在内存吃紧时 ENOMEM，且更慢）
CONT_DIR=/dbbackup        # 容器内路径，与 docker-compose.yml 的 ./data/backups/db:/dbbackup 对应
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

LATEST="$DUMP_DIR/latest"
PART="$LATEST.part"
# latest/latest.part 都是容器内产物（docker exec 默认 root，pg_dump 还把目录建成 0700），
# 宿主 bnbnyu 对其无写权限——宿主侧 rm/mv 轮换必踩 Permission denied（2026-09-15 实测）。
# 删除与轮换一律放容器内以 root 执行，宿主只负责读（restic 上传）。
docker exec "$CONTAINER" rm -rf "$CONT_DIR/latest.part"

log "开始 pg_dump 全量导出（Fd 目录格式 4 并行，容器内直写 bind mount，不停库）..."
docker exec "$CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" -Fd -j 4 -f "$CONT_DIR/latest.part"
# a+rwX：读给宿主 restic；写是保险，将来若再有宿主侧清理动作不再被权限卡死
docker exec "$CONTAINER" chmod -R a+rwX "$CONT_DIR/latest.part"
docker exec "$CONTAINER" sh -c "rm -rf '$CONT_DIR/latest' && mv '$CONT_DIR/latest.part' '$CONT_DIR/latest'"
log "导出完成（$(du -sh "$LATEST" | cut -f1)，$(ls "$LATEST" | wc -l) 个文件），开始 restic 上传（逐表去重，只传变化的表）..."

"$RESTIC" backup "$LATEST" --tag db-nightly

# 每周日执行保留策略：保留最近 30 个每日快照，清理过期数据块
if [ "$(date +%u)" = "7" ]; then
    log "周日：执行 forget --prune（保留 ${KEEP_DAILY} 天）..."
    "$RESTIC" forget --keep-daily "$KEEP_DAILY" --prune
fi

log "完成。最新快照："
"$RESTIC" snapshots --latest 1
