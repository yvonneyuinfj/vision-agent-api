#!/usr/bin/env bash
# ============================================================================
# start_metallography_tool.sh —— 启动本地金相(断口)真实工具（显微组织识别与定量分析 API）
#   → 127.0.0.1:18013（拓扑分配表 18013）
#
# 用法：bash scripts/dev/start_metallography_tool.sh
# 幂等：端口已在监听则跳过。日志 logs/dev/metallography.out，PID logs/dev/metallography.pid
# 前置：local-tools/metallography/.venv 已装好
#       （macOS：torch 2.6.0 / torchvision / scikit-image / pandas / opencv / fastapi）
# 适配：交付方 Gamma_UNet_Project_API（cpp_api_server，uvicorn api.api_server:app）
#       交付离线锁 torch==2.14+cpu 在 macOS 无兼容轮子 → 用 macOS 可装 torch 2.6.0 已实测模型加载/推理通过
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
M="$ROOT/local-tools/metallography"
RUN="$ROOT/logs/dev"
PORT="${PORT:-18013}"
mkdir -p "$RUN"

# 幂等：已在运行则跳过（健康响应携带 status=ok / model_ready 或 code:0）
if curl -s -m 3 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -qE '"model_ready"\s*:\s*true|"healthy"|"ok"'; then
  echo "[skip] metallography(:$PORT) 已在运行"
  exit 0
fi
[[ -x "$M/.venv/bin/python" ]] || { echo "[FAIL] 工具 venv 缺失：$M/.venv"; exit 1; }

export MPLCONFIGDIR="$RUN/matplotlib-metallography"
export MPLBACKEND=Agg
export PYTHONNOUSERSITE=1

cd "$M"
nohup "$M/.venv/bin/python" -m uvicorn api.api_server:app \
  --host 127.0.0.1 --port "$PORT" --workers 1 >"$RUN/metallography.out" 2>&1 &
echo $! > "$RUN/metallography.pid"

echo "[wait] 模型加载中（CPU，UNet 加载）…"
for _ in $(seq 1 120); do
  if curl -s -m 3 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -qE '"model_ready"\s*:\s*true|"healthy"|"ok"'; then
    echo "[ok]   metallography → 127.0.0.1:${PORT}（pid $(cat "$RUN/metallography.pid")）"
    curl -s -m 3 "http://127.0.0.1:$PORT/health" | head -c 400; echo
    exit 0
  fi
  sleep 1
done
echo "[FAIL] 健康检查超时（120s）—— 最近日志："
tail -20 "$RUN/metallography.out" || true
exit 1