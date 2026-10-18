#!/usr/bin/env bash
# ============================================================================
# stop_local_tools.sh —— 停止由 scripts/dev 启动的本地工具实例（mock×5 + 荧光）
# 只认 logs/dev/*.pid 记录的进程；不影响 :8002 旧 mock、:8010 平台或其他任何进程。
# 用法：bash scripts/dev/stop_local_tools.sh [名称…]   # 缺省 = 全部六个
# ============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN="$ROOT/logs/dev"
NAMES="${*:-xray fracture metallography borescope hotsection fluorescence}"

for name in $NAMES; do
  pidfile="$RUN/$name.pid"
  if [[ ! -f "$pidfile" ]]; then echo "[skip] ${name}：无 pid 文件"; continue; fi
  pid="$(cat "$pidfile")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" && echo "[stop] ${name}（pid ${pid}）"
  else
    echo "[skip] ${name}：pid ${pid} 已不在"
  fi
  rm -f "$pidfile"
done

sleep 2
echo "剩余监听（应为空或仅剩非本脚本进程）："
lsof -nP -iTCP:18011-18016 -sTCP:LISTEN 2>/dev/null || echo "  18011-18016 已全部释放"
