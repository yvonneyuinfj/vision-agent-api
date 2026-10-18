#!/usr/bin/env bash
# ============================================================================
# start_xray_tool.sh —— 启动本地 X光/DR 真实工具（DR Structure OPI Sequence API）
#   → 127.0.0.1:18011（拓扑分配表 18011）
#
# 用法：bash scripts/dev/start_xray_tool.sh
# 幂等：端口已在监听则跳过。日志 logs/dev/xray.log，PID logs/dev/xray.pid
# 前置：local-tools/xray/.venv 已装好（macOS 兼容依赖：torch 2.1.2 / numpy / fastapi / uvicorn）
# 适配：交付方 start_server.py（env HOST/PORT）→ nohup + uvicorn server.app:app 独立进程
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
X="$ROOT/local-tools/xray"
RUN="$ROOT/logs/dev"
PORT="${PORT:-18011}"
mkdir -p "$RUN"

# 幂等：已在运行则跳过（健康响应携带 "code": 0）
if curl -s -m 3 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -qE '"code"\s*:\s*0'; then
  echo "[skip] xray(:$PORT) 已在运行"
  exit 0
fi
[[ -x "$X/.venv/bin/python" ]] || { echo "[FAIL] 工具 venv 缺失：$X/.venv"; exit 1; }

export MPLCONFIGDIR="$RUN/matplotlib-xray"
export PYTHONNOUSERSITE=1
export DR_API_DEVICE=cpu

cd "$X"
nohup env DR_API_HOST=127.0.0.1 DR_API_PORT="$PORT" DR_API_DEVICE=cpu \
  "$X/.venv/bin/python" -m uvicorn server.app:app \
  --host 127.0.0.1 --port "$PORT" --workers 1 >"$RUN/xray.out" 2>&1 &
echo $! > "$RUN/xray.pid"

echo "[wait] 模型加载中（CPU，首次发布 checkpoint 可能较慢）…"
for _ in $(seq 1 120); do
  if curl -s -m 3 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -qE '"code"\s*:\s*0'; then
    echo "[ok]   xray → 127.0.0.1:${PORT}（pid $(cat "$RUN/xray.pid")）"
    curl -s -m 3 "http://127.0.0.1:$PORT/health" | head -c 400; echo
    exit 0
  fi
  sleep 1
done
echo "[FAIL] 健康检查超时（120s）—— 最近日志："
tail -15 "$RUN/xray.out" || true
exit 1