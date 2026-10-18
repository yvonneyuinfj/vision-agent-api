#!/usr/bin/env bash
# ============================================================================
# 平台数据备份（B′ 备份制度）
#
# 背景：平台的 MySQL 库与 MinIO 桶寄生在 RAGFlow Docker 套件内（生命周期耦合）。
# 触发时机（强制）：RAGFlow 套件升级 / compose 重建 / 服务器迁移 / 大规模变更前。
# 建议：验证联调期每日 cron 一次。
#
# 内容：
#   1) MySQL quality_inspection 全库 dump（--single-transaction，不锁表）；
#   2) MinIO 桶 qc-media-assets 镜像（mc mirror）；
#   3) SHA256 清单；按 KEEP 份数滚动清理。
#
# 用法（root）：bash deploy/backup_platform_data.sh
# 可覆盖变量：APP_DIR BACKUP_ROOT KEEP MYSQL_CONTAINER MC_IMAGE
#   园区网（目标机禁装 Docker）裸装形态：MYSQL_MODE=host + MYSQL_ROOT_PASS_FILE
#   （root 口令文件，chmod 600）；mc 用制品包内的静态二进制装到 /usr/local/bin。
# ============================================================================
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/agent/vision-agent/quality-agent}"   # 与 install_server.sh 保持一致
BACKUP_ROOT="${BACKUP_ROOT:-/opt/backups/quality-agent}"
KEEP="${KEEP:-7}"
MYSQL_MODE="${MYSQL_MODE:-container}"   # container=验证机 docker 形态；host=无 Docker 裸装（园区网）
MYSQL_CONTAINER="${MYSQL_CONTAINER:-docker-mysql-1}"
MC_IMAGE="${MC_IMAGE:-minio/mc:latest}"
MYSQL_HOST="${MYSQL_HOST:-127.0.0.1}"
MYSQL_PORT="${MYSQL_PORT:-3306}"
MYSQL_ROOT_PASS_FILE="${MYSQL_ROOT_PASS_FILE:-/opt/qc-mysql-root.pass}"
BUCKET_KEY="MINIO_BUCKET"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_ROOT/$STAMP"

log(){ printf '[backup] %s\n' "$*"; }
die(){ printf '[backup][ERROR] %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请以 root 运行"
[[ -f "$APP_DIR/.env" ]] || die "未找到 $APP_DIR/.env"
mkdir -p "$OUT"

# 从 .env 读取配置（取第一个空白前的 token，兼容行内注释残留）
read_env(){ sed -n "s/^$1=//p" "$APP_DIR/.env" | head -1 | awk '{print $1}'; }
MINIO_ENDPOINT="$(read_env MINIO_ENDPOINT)"
MINIO_ACCESS_KEY="$(read_env MINIO_ACCESS_KEY)"
MINIO_SECRET_KEY="$(read_env MINIO_SECRET_KEY)"
MINIO_BUCKET="$(read_env "$BUCKET_KEY")"; MINIO_BUCKET="${MINIO_BUCKET:-qc-media-assets}"

log "1/3 MySQL dump → $OUT/quality_inspection.sql.gz"
if [[ "$MYSQL_MODE" == host ]]; then
  # 无 Docker 裸装：宿主 mysqldump 直连 127.0.0.1（root 口令从 600 权限文件读，不落脚本）
  [[ -f "$MYSQL_ROOT_PASS_FILE" ]] || die "MYSQL_MODE=host 需 root 口令文件：$MYSQL_ROOT_PASS_FILE"
  MYSQL_ROOT_PASSWORD="$(head -1 "$MYSQL_ROOT_PASS_FILE")"
  mysqldump -h "$MYSQL_HOST" -P "$MYSQL_PORT" -uroot -p"$MYSQL_ROOT_PASSWORD" \
    --single-transaction --databases quality_inspection | gzip > "$OUT/quality_inspection.sql.gz"
else
  docker exec "$MYSQL_CONTAINER" sh -c \
    'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --databases quality_inspection' \
    | gzip > "$OUT/quality_inspection.sql.gz"
fi

log "2/3 MinIO 桶 $MINIO_BUCKET 镜像（endpoint=$MINIO_ENDPOINT）"
mkdir -p "$OUT/minio"
if command -v mc >/dev/null 2>&1; then
  mc alias set "qc-$STAMP" "http://$MINIO_ENDPOINT" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null
  mc mirror "qc-$STAMP/$MINIO_BUCKET" "$OUT/minio/$MINIO_BUCKET" >/dev/null
  mc alias rm "qc-$STAMP" >/dev/null || true
elif command -v docker >/dev/null 2>&1; then
  # 无宿主 mc：一次性容器（仅验证机；registry mirror 已配，手册 §5.3）
  docker run --rm --network host -v "$OUT/minio:/data" --entrypoint sh "$MC_IMAGE" -c \
    "mc alias set qclocal 'http://$MINIO_ENDPOINT' '$MINIO_ACCESS_KEY' '$MINIO_SECRET_KEY' && mc mirror qclocal/$MINIO_BUCKET /data/$MINIO_BUCKET"
else
  die "无 Docker 环境下需宿主 mc：把制品包内 mc 静态二进制装到 /usr/local/bin/mc（chmod +x）"
fi

log "3/3 校验清单 + 滚动清理（保留 $KEEP 份）"
( cd "$OUT" && find . -type f ! -name MANIFEST.sha256 -exec sha256sum {} + > MANIFEST.sha256 )
ls -1dt "$BACKUP_ROOT"/*/ 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -rf

log "完成：$OUT"
if [[ "$MYSQL_MODE" == host ]]; then
  cat <<EOF
恢复方法（无 Docker 裸装）：
  # MySQL（覆盖式恢复）：
  gunzip -c $OUT/quality_inspection.sql.gz | mysql -h $MYSQL_HOST -P $MYSQL_PORT -uroot -p"$(head -1 "$MYSQL_ROOT_PASS_FILE" | sed 's/./*/g')…口令见 $MYSQL_ROOT_PASS_FILE" 
  # MinIO（回灌桶，用宿主 mc）：
  mc alias set qclocal 'http://$MINIO_ENDPOINT' '$MINIO_ACCESS_KEY' '$MINIO_SECRET_KEY' && \\
  mc mirror --overwrite $OUT/minio/$MINIO_BUCKET qclocal/$MINIO_BUCKET && mc alias rm qclocal
EOF
else
  cat <<EOF
恢复方法：
  # MySQL（覆盖式恢复）：
  gunzip -c $OUT/quality_inspection.sql.gz | docker exec -i $MYSQL_CONTAINER sh -c 'exec mysql -uroot -p"\$MYSQL_ROOT_PASSWORD"'
  # MinIO（回灌桶）：
  docker run --rm --network host -v $OUT/minio:/data --entrypoint sh $MC_IMAGE -c \\
    "mc alias set qclocal 'http://$MINIO_ENDPOINT' '$MINIO_ACCESS_KEY' '$MINIO_SECRET_KEY' && mc mirror --overwrite /data/$MINIO_BUCKET qclocal/$MINIO_BUCKET"
EOF
fi
