#!/usr/bin/env bash
# ============================================================================
# install_ai_tool.sh —— /opt/agent/vision-agent/tools 单工具安装器（mock 实例或真实交付包）
#
# 用法（root，服务器上执行）：
#   install_ai_tool.sh <tool> <pkg_dir> [选项]
#     <tool>     xray|fracture|metallography|borescope|hotsection|fluorescence
#     <pkg_dir>  包目录，约定结构：
#                  start.sh        必需，解耦层（契约见 deploy/ai-tools/README.md）
#                  requirements.txt 在线安装依赖清单（--offline 时可省）
#                  tool.env        可选，额外环境变量（HOST/PORT/TOOL_NAME 之外的）
#                  其余文件        原样进 /opt/agent/vision-agent/tools/<tool>/app/
#   选项：
#     --tool-name <name>     tool.env 的 TOOL_NAME（默认 <tool>；mock 实例应设为
#                            平台能力 tool_name，映射表见 ai-tools/README.md）
#     --requirements <file>  pkg 内依赖清单相对路径（默认 requirements.txt）
#     --offline <wheels_dir> 离线安装：--no-index --find-links（交付方 wheels 目录），
#                            跳过镜像源；依赖清单仍取 --requirements
#     --no-start             只安装不启动（人工检查后 systemctl start）
#
# 幂等：重复执行 = 更新 app/ + 补装依赖 + 重启实例。venv 已存在则复用。
# 环境假设（与平台装机一致）：/usr/local/bin/python3.11、/opt/uv-tool/bin/uv、
#   工具账户已建（env-prep/init_ai_tools_env.sh）、阿里云 PyPI 镜像可达（在线模式）。
# ============================================================================
set -euo pipefail

log(){ printf '[install-ai-tool] %s\n' "$*"; }
die(){ printf '[install-ai-tool][ERROR] %s\n' "$*" >&2; exit 1; }
usage(){ sed -n '3,24p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

TOOL="${1:-}"; PKG="${2:-}"
[[ -n "$TOOL" && -n "$PKG" ]] || usage
shift 2

TOOL_NAME_ENV="$TOOL"
REQ_REL="requirements.txt"
OFFLINE_DIR=""
NO_START=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tool-name)    TOOL_NAME_ENV="${2:?}"; shift 2;;
    --requirements) REQ_REL="${2:?}"; shift 2;;
    --offline)      OFFLINE_DIR="${2:?}"; shift 2;;
    --no-start)     NO_START=1; shift;;
    *) die "未知选项：$1";;
  esac
done

case "$TOOL" in
  xray)          PORT=18011;;
  fracture)      PORT=18012;;
  metallography) PORT=18013;;
  borescope)     PORT=18014;;
  hotsection)    PORT=18015;;
  fluorescence)  PORT=18016;;
  *) die "未知工具：$TOOL（合法：xray fracture metallography borescope hotsection fluorescence）";;
esac

ROOT=/opt/agent/vision-agent/tools
DIR="$ROOT/$TOOL"
UV=/opt/uv-tool/bin/uv
PY=/usr/local/bin/python3.11
MIRROR="https://mirrors.cloud.aliyuncs.com/pypi/simple/"
MIRROR_HOST="mirrors.cloud.aliyuncs.com"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONSTRAINTS="$SCRIPT_DIR/ai-tools/constraints-glibc217.txt"
UNIT_SRC="$SCRIPT_DIR/systemd/ai-tool@.service"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-120}"

[[ $EUID -eq 0 ]] || die "请以 root 运行"
id -u "$TOOL" >/dev/null 2>&1 || die "账户 $TOOL 不存在（先跑 deploy/env-prep/init_ai_tools_env.sh）"
[[ -d "$PKG" ]] || die "包目录不存在：$PKG"
[[ -f "$PKG/start.sh" ]] || die "包缺 start.sh（解耦层契约见 deploy/ai-tools/README.md）"
[[ -x "$UV" ]] || die "缺 uv：$UV"
[[ -x "$PY" ]] || die "缺 Python 3.11：$PY"

log "1/7 目标 $DIR（$TOOL → 127.0.0.1:$PORT，TOOL_NAME=$TOOL_NAME_ENV）"

log "2/7 同步包内容 → $DIR/app/"
mkdir -p "$DIR/app"
# 幂等覆盖：先清旧 app（保留 venv/tool.env/start.sh 在 app 之外，不受影响）
find "$DIR/app" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
cp -a "$PKG/." "$DIR/app/"
rm -f "$DIR/app/start.sh"   # start.sh 唯一入口在 $DIR/start.sh（单一来源）

log "3/7 start.sh 解耦层"
install -m 750 -o "$TOOL" -g "$TOOL" "$PKG/start.sh" "$DIR/start.sh"

log "4/7 venv（复用已存在的）+ 依赖安装"
if [[ ! -x "$DIR/.venv/bin/python" ]]; then
  "$UV" venv --python "$PY" "$DIR/.venv"
fi
REQ="$DIR/app/$REQ_REL"
if [[ -f "$REQ" ]]; then
  if [[ -n "$OFFLINE_DIR" ]]; then
    [[ -d "$OFFLINE_DIR" ]] || die "--offline wheels 目录不存在：$OFFLINE_DIR"
    "$UV" pip install --python "$DIR/.venv/bin/python" \
      --no-index --find-links "$OFFLINE_DIR" \
      -r "$REQ"
  else
    "$UV" pip install --python "$DIR/.venv/bin/python" \
      --index-url "$MIRROR" --allow-insecure-host "$MIRROR_HOST" \
      -c "$CONSTRAINTS" \
      -r "$REQ"
  fi
else
  log "  无 $REQ_REL —— 跳过依赖安装（离线交付包自带安装器时属正常）"
fi

log "5/7 tool.env（pkg 自带额外变量在前，拓扑三键在后覆盖）"
{
  [[ -f "$DIR/app/tool.env" ]] && cat "$DIR/app/tool.env"
  printf 'HOST=127.0.0.1\nPORT=%s\nTOOL_NAME=%s\n' "$PORT" "$TOOL_NAME_ENV"
} > "$DIR/tool.env"
chown root:"$TOOL" "$DIR/tool.env"; chmod 640 "$DIR/tool.env"

log "6/7 属主/权限 + systemd 单元"
chown -R "$TOOL:$TOOL" "$DIR"
chmod 750 "$DIR"
if [[ ! -f /etc/systemd/system/ai-tool@.service ]] \
   || ! cmp -s "$UNIT_SRC" /etc/systemd/system/ai-tool@.service; then
  install -m 644 "$UNIT_SRC" /etc/systemd/system/ai-tool@.service
  log "  单元已更新 → daemon-reload"
fi
systemctl daemon-reload

if [[ "$NO_START" == 1 ]]; then
  log "7/7 --no-start：跳过启动。人工检查后：systemctl enable --now ai-tool@$TOOL"
  exit 0
fi

log "7/7 启动 ai-tool@$TOOL 并轮询 /health（最长 ${HEALTH_TIMEOUT}s）"
systemctl enable "ai-tool@$TOOL" >/dev/null 2>&1 || true
systemctl restart "ai-tool@$TOOL"
for ((i=1; i<=HEALTH_TIMEOUT; i++)); do
  BODY="$(curl -s -m 3 "http://127.0.0.1:$PORT/health" 2>/dev/null || true)"
  if [[ "$BODY" == *'"code"'* || "$BODY" == *'"status"'* ]]; then
    log "  health OK（${i}s）：$(printf '%s' "$BODY" | head -c 160)"
    systemctl --no-pager -l status "ai-tool@$TOOL" | head -5
    cat <<EOF

[install-ai-tool] 完成：$TOOL → 127.0.0.1:$PORT
  日志：journalctl -u ai-tool@$TOOL -f
  平台侧切换端点：deploy/switch_tool_endpoint.sh <capability_id> http://127.0.0.1:$PORT
  回滚：systemctl stop ai-tool@$TOOL && switch_tool_endpoint.sh 切回原端点
EOF
    exit 0
  fi
  sleep 1
done
systemctl --no-pager -l status "ai-tool@$TOOL" | head -10 || true
journalctl -u "ai-tool@$TOOL" -n 20 --no-pager || true
die "health 轮询超时（${HEALTH_TIMEOUT}s）——上方为服务状态与最近日志"
