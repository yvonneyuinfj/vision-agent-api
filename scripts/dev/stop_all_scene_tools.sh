#!/usr/bin/env bash
# ============================================================================
# stop_all_scene_tools.sh —— 一键停止本地检测场景工具 + 平台
#
# 与 scripts/dev/start_all_scene_tools.sh 对称：停止 X光/金相/孔探/热端/荧光 5 个真实工具、
# 可选 mock（当前仅 fracture:18012 这一未交付槽位），以及平台(:8010)。
#
# 用法：
#   bash scripts/dev/stop_all_scene_tools.sh            # 停止 5 真实工具 + 平台（及 mock）
#   bash scripts/dev/stop_all_scene_tools.sh --keep-platform  # 只停工具，保留平台
#   bash scripts/dev/stop_all_scene_tools.sh xray borescope  # 只停指定工具（位置参数）
#
# 幂等：无 pid 文件 / 进程已不在则跳过。
# ============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN="$ROOT/logs/dev"
KEEP_PLATFORM=0
ARGS=()

for arg in "$@"; do
  case "$arg" in
    --keep-platform) KEEP_PLATFORM=1 ;;
    --tools-only)    : ;; # 说明性参数：指定工具名直接作为位置参数传入
    *) ARGS+=("$arg") ;;
  esac
done

# 工具名（PID 文件名对应 logs/dev/<tool>.pid）
# fracture 是 mock 槽位（真实断口工具未交付），其余 5 个为已交付真工具
ALL_TOOLS="xray metallography borescope hotsection fracture fluorescence"

if [[ "${#ARGS[@]}" -gt 0 ]]; then
  TOOLS=("${ARGS[@]}")
else
  TOOLS=($ALL_TOOLS)
fi

echo "==== 停止检测场景工具（本地 development 布局） ===="
for name in "${TOOLS[@]}"; do
  pidfile="$RUN/$name.pid"
  if [[ ! -f "$pidfile" ]]; then echo "  [skip] ${name}：无 pid 文件"; continue; fi
  pid="$(cat "$pidfile")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" && echo "  [stop] ${name}（pid ${pid}）"
  else
    echo "  [skip] ${name}：pid ${pid} 已不在"
  fi
  rm -f "$pidfile"
done

if [[ "$KEEP_PLATFORM" -eq 1 ]]; then
  echo "  [keep] 平台保留（--keep-platform）"
else
  echo "==== 停止平台(:8010) ===="
  pfile="$RUN/platform8010.pid"
  if [[ ! -f "$pfile" ]]; then
    echo "  [skip] 平台：无 pid 文件"
  else
    pid="$(cat "$pfile")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" && echo "  [stop] 平台（pid ${pid}）"
    else
      echo "  [skip] 平台：pid ${pid} 已不在"
    fi
    rm -f "$pfile"
  fi
fi

sleep 2
echo "---- 探活确认（按端口监听） ----"
for pn in "18011:xray" "18012:fracture(mock)" "18013:metallography" "18014:borescope" "18015:hotsection" "18016:fluorescence" "8010:平台"; do
  port="${pn%%:*}"; lbl="${pn##*:}"
  if lsof -nP -iTCP:$port -sTCP:LISTEN >/dev/null 2>&1; then
    echo "  [still] ${lbl} :${port} 仍在监听"
  else
    echo "  [free]  ${lbl} :${port} 已释放"
  fi
done
echo "完成。"
