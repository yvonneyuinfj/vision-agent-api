#!/usr/bin/env bash
# ============================================================================
# start_fluorescence_tool.sh —— 启动本地荧光真实工具（blade_defect_detector）
#   → 127.0.0.1:18016（拓扑同服务器 Op-C；PORT 可用环境变量覆盖）
#
# 用法：bash scripts/dev/start_fluorescence_tool.sh
# 幂等：端口已在监听则跳过。日志 logs/dev/fluorescence.log，PID logs/dev/fluorescence.pid
# 前置：local-tools/fluorescence/.venv 已装好（uv + requirements-macos.txt + yolox-src.pth，
#       安装过程见 deploy/ai-tools/fluorescence/README-fluorescence.md）
# 环境变量语义与交付方一致：CUDA_VISIBLE_DEVICES=""（纯 CPU）、PYTHONPATH（yolox_project）、
#   PUBLIC_BASE_URL（产物绝对 URI 基址，必须与实际端口一致）、REPORT_FONT_PATH（PDF 中文字体）
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
F="$ROOT/local-tools/fluorescence"
A="$F/app/thread-defect-api-20260910"
RUN="$ROOT/logs/dev"
PORT="${PORT:-18016}"
mkdir -p "$RUN"

if curl -s -m 2 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"code":0'; then
  echo "[skip] fluorescence(:$PORT) 已在运行"
  exit 0
fi
[[ -x "$F/.venv/bin/python" ]] || { echo "[FAIL] 工具 venv 缺失：$F/.venv"; exit 1; }
[[ -d "$A/server" ]] || { echo "[FAIL] 应用目录缺失：$A"; exit 1; }

cd "$A"
CUDA_VISIBLE_DEVICES="" \
PYTHONPATH="$A/labels/yolox_project" \
PUBLIC_BASE_URL="http://127.0.0.1:$PORT" \
REPORT_FONT_PATH="${REPORT_FONT_PATH:-/System/Library/Fonts/Supplemental/Songti.ttc}" \
  nohup "$F/.venv/bin/python" -m uvicorn server.app:app \
    --host 127.0.0.1 --port "$PORT" --workers 1 >>"$RUN/fluorescence.log" 2>&1 &
echo $! > "$RUN/fluorescence.pid"

echo "[wait] 模型加载中（CPU，约 5-30s）…"
for _ in $(seq 1 60); do
  if curl -s -m 2 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"code":0'; then
    echo "[ok]   fluorescence → 127.0.0.1:${PORT}（pid $(cat "$RUN/fluorescence.pid")）"
    curl -s -m 2 "http://127.0.0.1:$PORT/health" | head -c 220; echo
    exit 0
  fi
  sleep 1
done
echo "[FAIL] 健康检查超时（60s）—— 最近日志："
tail -8 "$RUN/fluorescence.log" || true
exit 1
