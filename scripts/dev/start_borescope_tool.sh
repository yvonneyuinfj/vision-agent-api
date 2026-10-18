#!/usr/bin/env bash
# ============================================================================
# start_borescope_tool.sh —— 启动本地孔探真实工具（borescope 叶片缺陷识别）
#   → 127.0.0.1:18014（拓扑分配表 18014）
#
# 用法：bash scripts/dev/start_borescope_tool.sh
# 幂等：端口已在监听则跳过。日志 logs/dev/borescope.log，PID logs/dev/borescope.pid
# 前置：local-tools/borescope/.venv 已装好（macOS 兼容依赖：numpy/opencv-headless/onnxruntime）
# 实现：交付方 scripts/start.sh 适配为 .venv 布局 + nohup 独立进程（同荧光先例）
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
B="$ROOT/local-tools/borescope"
ENTRY="$B/app/api_server.py"
RUN="$ROOT/logs/dev"
PORT="${PORT:-18014}"
mkdir -p "$RUN"

# 幂等：已在运行则跳过（健康响应携带 "status":"healthy" 或 "code": 0）
if curl -s -m 2 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -qE '"status"\s*:\s*"healthy"|"code"\s*:\s*0'; then
  echo "[skip] borescope(:$PORT) 已在运行"
  exit 0
fi
[[ -x "$B/.venv/bin/python" ]] || { echo "[FAIL] 工具 venv 缺失：$B/.venv"; exit 1; }
[[ -f "$ENTRY" ]] || { echo "[FAIL] 应用入口缺失：$ENTRY"; exit 1; }

mkdir -p "$B/logs"
cd "$B"
nohup "$B/.venv/bin/python" "$ENTRY" --host 127.0.0.1 --port "$PORT" >"$RUN/borescope.out" 2>&1 &
echo $! > "$RUN/borescope.pid"

echo "[wait] 模型加载中（ONNX CPU）…"
for _ in $(seq 1 40); do
  if curl -s -m 2 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -qE '"status"\s*:\s*"healthy"|"code"\s*:\s*0'; then
    echo "[ok]   borescope → 127.0.0.1:${PORT}（pid $(cat "$RUN/borescope.pid")）"
    curl -s -m 2 "http://127.0.0.1:$PORT/health" | head -c 300; echo
    exit 0
  fi
  sleep 1
done
echo "[FAIL] 健康检查超时（40s）—— 最近日志："
tail -8 "$RUN/borescope.out" || true
exit 1