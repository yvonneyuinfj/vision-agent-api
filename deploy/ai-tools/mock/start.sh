#!/usr/bin/env bash
# start.sh 解耦层 —— mock 实例（mock_service/main.py 单进程多域，按实例端口/身份启动）
# 契约：systemd ai-tool@<tool> 前台调用；HOST/PORT/TOOL_NAME 由 tool.env 注入；
#       venv 在 ../.venv（install_ai_tool.sh 布局）；日志走 stdout → journald。
set -euo pipefail
cd "$(dirname "$0")/app"
exec ../.venv/bin/python -m uvicorn main:app \
  --host "${HOST:-127.0.0.1}" \
  --port "${PORT:?tool.env 必须提供 PORT}" \
  --workers 1
