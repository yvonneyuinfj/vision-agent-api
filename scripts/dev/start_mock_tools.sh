#!/usr/bin/env bash
# ============================================================================
# start_mock_tools.sh —— 启动本地 mock 工具实例（一场景一端口，拓扑同服务器 Op-B）
#
#   xray:18011(dr) fracture:18012 metallography:18013 borescope:18014 hotsection:18015(infrared)
#
# 2026-09-28 起：18011/18013/18014/18015 已由 5 个**真实**交付工具占位（含 18016 荧光），
# 本脚本实际只补 `fracture:18012` 这一未交付槽位；其余端口已被监听时按端口占用跳过。
# 荧光(fpi) 已有真工具 18016，**不列入 mock 实例表**。
#
# 用法：bash scripts/dev/start_mock_tools.sh
# 幂等：**按端口监听**跳过（不能按 /health 载荷判断——真工具载荷形态各异，
#       如孔探 18014 返回 `{"status":"healthy"}` 无 `"code":0`，按载荷判断会撞端口后 exit 1）。
#       日志 logs/dev/mock-<tool>.log，PID logs/dev/<tool>.pid
# 停止：bash scripts/dev/stop_local_tools.sh
# 依赖：项目 .venv（fastapi/uvicorn/pillow），mock_service/main.py
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VENV="$ROOT/.venv/bin/python"
RUN="$ROOT/logs/dev"
mkdir -p "$RUN"

[[ -x "$VENV" ]] || { echo "[FAIL] 项目 venv 缺失：$VENV"; exit 1; }

INSTANCES="xray:18011:dr_defect_detection
fracture:18012:fracture_defect_detection
metallography:18013:metallography_defect_detection
borescope:18014:borescope_defect_detection
hotsection:18015:infrared_defect_detection"

while IFS=: read -r tool port tname; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[skip] $tool(:$port) 端口已被占用（真工具或在跑的 mock），不重复启动"
    continue
  fi
  cd "$ROOT/mock_service"
  HOST=127.0.0.1 PORT="$port" TOOL_NAME="$tname" \
    nohup "$VENV" main.py >>"$RUN/mock-$tool.log" 2>&1 &
  echo $! > "$RUN/$tool.pid"
  cd "$ROOT"
  ok=0
  for _ in $(seq 1 15); do
    if curl -s -m 1 "http://127.0.0.1:$port/health" 2>/dev/null | grep -q '"code":0'; then ok=1; break; fi
    sleep 1
  done
  if [[ $ok -eq 1 ]]; then
    echo "[ok]   ${tool} → 127.0.0.1:${port}（${tname}，pid $(cat "$RUN/$tool.pid")）"
  else
    echo "[FAIL] $tool(:$port) 健康检查超时 —— 见 $RUN/mock-$tool.log"
    tail -5 "$RUN/mock-$tool.log" || true
    exit 1
  fi
done <<< "$INSTANCES"

echo "全部就绪。平台侧若端点未指向这些端口，执行能力切换（本地示例见 deploy/switch_tool_endpoint.sh 注释）。"
