#!/usr/bin/env bash
# ============================================================================
# switch_tool_endpoint.sh —— 能力端点切换（mock ↔ 真实工具）+ 热加载 + 回读验证
#
# 用法（root，服务器上执行）：
#   switch_tool_endpoint.sh <capability_id> <endpoint_url> [health_url] [meta_json]
#
#   capability_id  如 fpi_defect_detection_v1（六场景映射见 deploy/README §5）
#   endpoint_url   如 http://127.0.0.1:18016（工具必须绑定 127.0.0.1）
#   health_url     默认 <endpoint_url>/health
#   meta_json      可选；整体替换 meta_json 列（真实工具参数契约不同时必须给全量，
#                  含 families/default_params/input_required/max_images/note；
#                  JSON 内部只允许双引号，禁止单引号）
#
# 行为（对齐 deploy/README §5 端点切换约定）：
#   1) UPDATE capability_registry：endpoint/health_endpoint（+可选 meta_json），version+1
#   2) 回读 SELECT 验证落库
#   3) POST /api/v1/capabilities/reload 热加载（不等 15s 轮询）
#   4) 平台 API 回读 + 新端点 /health 探活
#
# 安全：MySQL root 口令只在容器内展开（$MYSQL_ROOT_PASSWORD），本机不回显。
# 回滚：对同一 capability_id 用旧端点重跑本脚本即可（version 再 +1，幂等安全）。
# ============================================================================
set -euo pipefail

log(){ printf '[switch-endpoint] %s\n' "$*"; }
die(){ printf '[switch-endpoint][ERROR] %s\n' "$*" >&2; exit 1; }

CAP="${1:-}"; EP="${2:-}"; HEALTH_EP="${3:-}"; META_JSON="${4:-}"
[[ -n "$CAP" && -n "$EP" ]] || { sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }
[[ -z "$HEALTH_EP" ]] && HEALTH_EP="${EP%/}/health"

CONTAINER="${MYSQL_CONTAINER:-docker-mysql-1}"
DB="${PLATFORM_DB:-quality_inspection}"
PLATFORM_BASE="${PLATFORM_BASE:-http://127.0.0.1:80}"
PY=/usr/local/bin/python3.11

[[ $EUID -eq 0 ]] || die "请以 root 运行"
docker ps --format '{{.Names}}' | grep -qx "$CONTAINER" || die "MySQL 容器未运行：$CONTAINER"
case "$EP" in http://127.0.0.1:*|http://localhost:*) ;; *) die "endpoint 必须绑定本机回环（$EP）";; esac
[[ "$META_JSON" == *"'"* ]] && die "meta_json 内禁止单引号（SQL 字符串安全）"

run_sql() {
  docker exec -i "$CONTAINER" sh -c \
    "mysql -uroot -p\"\$MYSQL_ROOT_PASSWORD\" --default-character-set=utf8mb4 -t $DB" <<<"$1"
}

log "1/4 UPDATE capability_registry（$CAP → $EP，version+1）"
SQL="UPDATE capability_registry SET endpoint='$EP', health_endpoint='$HEALTH_EP'"
[[ -n "$META_JSON" ]] && SQL="$SQL, meta_json='$META_JSON'"
SQL="$SQL, version=version+1 WHERE capability_id='$CAP';"
run_sql "$SQL" >/dev/null

log "2/4 回读落库值"
ROW="$(run_sql "SELECT capability_id, endpoint, health_endpoint, version, status FROM capability_registry WHERE capability_id='$CAP';")"
echo "$ROW"
echo "$ROW" | grep -q "$EP" || die "回读未见新 endpoint —— UPDATE 未生效？"

log "3/4 热加载能力注册中心"
curl -s -m 10 -X POST "$PLATFORM_BASE/api/v1/capabilities/reload" | head -c 200; echo

log "4/4 平台视角回读 + 新端点探活"
curl -s -m 10 "$PLATFORM_BASE/api/v1/capabilities" | "$PY" - "$CAP" "$EP" <<'PYEOF'
import json, sys
cap, ep = sys.argv[1], sys.argv[2]
items = (json.load(sys.stdin).get("result") or {}).get("items") or []
row = next((c for c in items if c.get("capability_id") == cap), None)
if row is None:
    sys.exit(f"[switch-endpoint][ERROR] 平台能力列表无 {cap}")
print("[switch-endpoint] 平台已加载:", json.dumps(row, ensure_ascii=False))
PYEOF
HEALTH_BODY="$(curl -s -m 10 "$HEALTH_EP" || true)"
if [[ "$HEALTH_BODY" == *'"code"'* || "$HEALTH_BODY" == *'"status"'* ]]; then
  log "  新端点 /health OK：$(printf '%s' "$HEALTH_BODY" | head -c 160)"
else
  die "新端点 /health 无响应或形态异常（$HEALTH_EP）——请先排查工具实例，必要时回滚"
fi
log "完成：$CAP → $EP（建议随后提交一次真实任务做 E2E 验证）"
