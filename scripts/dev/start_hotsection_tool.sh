#!/usr/bin/env bash
# ============================================================================
# start_hotsection_tool.sh —— 启动本地热端部件真实工具（Hot-end 超分 + 缺陷检测 API）
#   → 127.0.0.1:18015（拓扑分配表 18015）
#
# 用法：bash scripts/dev/start_hotsection_tool.sh
# 幂等：端口已在监听则跳过。日志 logs/dev/hotsection.out，PID logs/dev/hotsection.pid
# 前置：local-tools/hotsection/.venv 已装好（macOS：torch 2.1.2 / torchvision / opencv / fastapi）
# 适配：交付方 start_server_nfscns.sh（HOST/PORT）→ nohup + uvicorn server.app:app 独立进程
# 注意：API 需 X-API-Key 请求头（默认 hot-end-local-key，见 .env）
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
H="$ROOT/local-tools/hotsection"
RUN="$ROOT/logs/dev"
PORT="${PORT:-18015}"
mkdir -p "$RUN"

# 幂等：已在运行则跳过（健康响应携带 "status":healthy）
if curl -s -m 3 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"healthy"'; then
  echo "[skip] hotsection(:$PORT) 已在运行"
  exit 0
fi
[[ -x "$H/.venv/bin/python" ]] || { echo "[FAIL] 工具 venv 缺失：$H/.venv"; exit 1; }

export PYTHONPATH="$H"
export PYTHONNOUSERSITE=1
export CUDA_VISIBLE_DEVICES=""
export MPLCONFIGDIR="$RUN/matplotlib-hotsection"
export HOT_END_RESULTS_DIR="${HOT_END_RESULTS_DIR:-$H/runtime/results}"
export HOT_END_LOG_DIR="${HOT_END_LOG_DIR:-$H/runtime/logs}"
export OMP_NUM_THREADS=4
export MKL_NUM_THREADS=4

mkdir -p "$H/runtime/logs" "$H/runtime/results"

cd "$H"
nohup "$H/.venv/bin/python" -m uvicorn server.app:app \
  --host 127.0.0.1 --port "$PORT" --workers 1 >"$RUN/hotsection.out" 2>&1 &
echo $! > "$RUN/hotsection.pid"

echo "[wait] 模型加载中（CPU，RealESRGAN 加载较慢）…"
for _ in $(seq 1 180); do
  if curl -s -m 3 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"healthy"'; then
    echo "[ok]   hotsection → 127.0.0.1:${PORT}（pid $(cat "$RUN/hotsection.pid")）"
    curl -s -m 3 -H "X-API-Key: hot-end-local-key" "http://127.0.0.1:$PORT/health" | head -c 400; echo
    exit 0
  fi
  sleep 1
done
echo "[FAIL] 健康检查超时（180s）—— 最近日志："
tail -15 "$RUN/hotsection.out" || true
exit 1