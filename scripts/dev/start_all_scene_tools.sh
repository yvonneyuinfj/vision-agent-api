#!/usr/bin/env bash
# ============================================================================
# start_all_scene_tools.sh —— 一键启动全部检测场景工具 + 质检智能体平台(8010)
#
# 本地一键（本地开发/联调）：启动 5 个已交付真实场景工具
#   （X光18011/金相18013/孔探18014/热端18015/荧光18016），全部就绪后启动平台(:8010)。
#
# 用法：
#   bash scripts/dev/start_all_scene_tools.sh                 # 默认：5 真实工具 + 平台
#   bash scripts/dev/start_all_scene_tools.sh --with-mock     # 追加 mock（当前仅补 fracture 断口——唯一未交付场景；与真实工具同端口冲突自动跳过）
#   bash scripts/dev/start_all_scene_tools.sh --no-platform   # 只起工具层，不起平台
#
# 幂等：每个工具脚本/平台各自幂等（端口已在监听则跳过）。
# 说明：
#   - 本脚本面向「本地 development 布局」，工具代码在 QualityCheckingAgent/local-tools/<tool>，
#     用 scripts/dev/start_<tool>_tool.sh 启动；
#   - 阿里云服务器生产布局（/opt/agent/vision-agent/tools/<tool>，systemd ai-tool@<tool>）见
#     scripts/dev/README.md 的「部署布局：local-tools vs /opt/agent/vision-agent/tools」一节与 deploy/。
# ============================================================================
set -eo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PORT="${PLATFORM_PORT:-8010}"
NO_PLATFORM=0
WITH_MOCK=0

for arg in "$@"; do
  case "$arg" in
    --no-platform) NO_PLATFORM=1 ;;
    --with-mock)   WITH_MOCK=1 ;;
    --skip-mock)   echo "[info] --skip-mock 为默认行为，忽略（默认不启用 mock）";;
    *) echo "[warn] 未知参数: $arg" ;;
  esac
done

echo "==================================================================="
echo " 质检智能体平台 · 场景工具一键启动（本地 development 布局）"
echo "==================================================================="
echo "工具目录:  $ROOT/local-tools/{xray,metallography,borescope,hotsection,fluorescence}"
echo "平台端口:  $PORT (uvicorn app.main:app)"
echo "-------------------------------------------------------------------"

# ---- 1. 真实场景工具（5 个交付真工具：X光18011/金相18013/孔探18014/热端18015/荧光18016）----
# 荧光曾缺席本循环：头注释把 fpi 记为"未交付场景"，但 start_fluorescence_tool.sh
# 与 local-tools/fluorescence/.venv 都在，且 2026-09-28 实跑 18016 通过（决策 47 取证）。
echo "[1/3] 启动真实检测场景工具（X光18011/金相18013/孔探18014/热端18015/荧光18016）…"
for tool in xray metallography borescope hotsection fluorescence; do
  if [[ -x "$ROOT/scripts/dev/start_${tool}_tool.sh" ]]; then
    echo "  → ${tool} …"
    bash "$ROOT/scripts/dev/start_${tool}_tool.sh"
  else
    echo "  [missing] start_${tool}_tool.sh 不存在"
  fi
done

# ---- 2. mock（可选；默认不启动）----
if [[ "$WITH_MOCK" -eq 1 ]]; then
  echo "[2/3] 追加 mock（当前仅 fracture 断口未交付；与真实工具同端口冲突自动跳过）…"
  if [[ -x "$ROOT/scripts/dev/start_mock_tools.sh" ]]; then
    bash "$ROOT/scripts/dev/start_mock_tools.sh" || echo "  [warn] mock 启动部分失败（不影响已起的真工具）"
  else
    echo "  [missing] start_mock_tools.sh"
  fi
else
  echo "[2/3] 跳过 mock（默认仅 5 真实工具；需要时加 --with-mock）"
fi

# ---- 3. 平台 ----
if [[ "$NO_PLATFORM" -eq 1 ]]; then
  echo "[3/3] 跳过平台（--no-platform）"
else
  echo "[3/3] 启动平台 :$PORT …"
  mkdir -p "$ROOT/logs/dev"
  if curl -s -m 2 "http://127.0.0.1:$PORT/api/v1/health" >/dev/null 2>&1; then
    echo "  [skip] 平台 :$PORT 已在运行"
  else
    cd "$ROOT"
    nohup "$ROOT/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port "$PORT" \
      >"$ROOT/logs/dev/platform$PORT.out" 2>&1 &
    echo $! > "$ROOT/logs/dev/platform$PORT.pid"
    echo "  [ok] 平台进程 pid $(cat "$ROOT/logs/dev/platform$PORT.pid")（日志 logs/dev/platform$PORT.out）"
  fi
fi

echo "-------------------------------------------------------------------"
echo " 探活（实际运行端口监听）："
curl -s -m 3 http://127.0.0.1:18011/health -o /dev/null -w '  X光/DR    :18011  %{http_code}\n' 2>/dev/null || echo "  X光/DR    :18011  未监听"
curl -s -m 3 http://127.0.0.1:18013/health -o /dev/null -w '  金相断口  :18013  %{http_code}\n' 2>/dev/null || echo "  金相断口  :18013  未监听"
curl -s -m 3 http://127.0.0.1:18014/health -o /dev/null -w '  孔探      :18014  %{http_code}\n' 2>/dev/null || echo "  孔探      :18014  未监听"
curl -s -m 3 -H "X-API-Key: hot-end-local-key" http://127.0.0.1:18015/health -o /dev/null -w '  热端部件  :18015  %{http_code}\n' 2>/dev/null || echo "  热端部件  :18015  未监听"
curl -s -m 3 http://127.0.0.1:18016/health -o /dev/null -w '  荧光渗透  :18016  %{http_code}\n' 2>/dev/null || echo "  荧光渗透  :18016  未监听"
[[ "$NO_PLATFORM" -eq 1 ]] || curl -s -m 3 "http://127.0.0.1:$PORT/api/v1/health" -o /dev/null -w "  平台      :$PORT  %{http_code}\n" 2>/dev/null || echo "  平台      :$PORT  未监听"
echo "==================================================================="
echo "完成。停止：bash scripts/dev/stop_local_tools.sh （平台另用 kill \$(cat logs/dev/platform$PORT.pid)）"