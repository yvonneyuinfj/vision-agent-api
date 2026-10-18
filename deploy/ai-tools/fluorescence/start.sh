#!/usr/bin/env bash
# start.sh 解耦层 —— 荧光真实工具（blade_defect_detector / thread-defect-api-20260910）
#
# 改编自交付方 start_server_nfscns.sh，差异项（详见 README-fluorescence.md §偏差）：
#   1) 路径：/opt/industrial-api → /opt/agent/vision-agent/tools/fluorescence（BASE 自解析）；
#   2) venv：/opt/venvs/industrial-api → $BASE/.venv（install_ai_tool.sh 布局，
#      不再 source activate，直接用 venv 解释器绝对路径）；
#   3) YOLOX 装载：交付方为 pip editable + 预编译 FastCOCOEvalOp；服务器 gcc 4.8.5
#      无法编译 torch 2.1.2 C++ 扩展（C++17），改 PYTHONPATH 源码直挂（本地已验证：
#      yolox 0.3.0 导入正常、推理/产物全链路通过；该算子仅 COCO 评估用，推理不依赖）；
#   4) 端口：8000 → ${PORT:-18016}（tool.env 注入，绑定 127.0.0.1）；
#   5) 其余环境变量（CUDA_VISIBLE_DEVICES/PUBLIC_BASE_URL/HOST）语义与交付方一致，
#      REPORT_FONT_PATH/TZ 等经 tool.env 注入。
set -euo pipefail

BASE="$(cd "$(dirname "$0")" && pwd)"
APP="$BASE/app/thread-defect-api-20260910"

[[ -d "$APP/server" ]] || { echo "[start] 应用目录缺失：$APP" >&2; exit 1; }
[[ -x "$BASE/.venv/bin/python" ]] || { echo "[start] venv 缺失：$BASE/.venv" >&2; exit 1; }

cd "$APP"

export CUDA_VISIBLE_DEVICES=""
export PYTHONPATH="$APP/labels/yolox_project/YOLOX:$APP/labels/yolox_project${PYTHONPATH:+:$PYTHONPATH}"
export PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://127.0.0.1:${PORT:-18016}}"

exec "$BASE/.venv/bin/python" -m uvicorn server.app:app \
  --host "${HOST:-127.0.0.1}" \
  --port "${PORT:-18016}" \
  --workers 1
