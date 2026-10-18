#!/usr/bin/env bash
# ============================================================================
# 智能质检 Agent 平台 —— 服务器非容器化安装脚本（B′）
#
# 前提：
#   1) deploy/env-prep/install_python311.sh 已完成（python3.11 可用）；
#   2) RAGFlow Docker 套件运行中（MySQL localhost:3306 / MinIO localhost:9000，
#      容器名 docker-mysql-1 / docker-minio-1，见部署手册 §10）；
#   3) 源码已上传到 SRC_DIR；前端静态产物 frontend/dist（dist 不入 git）应为
#      make_offline_bundle.sh 构建并植入的**顶层 Vue3 深蓝版**产物。
#      【前后端分离】后端 app/main.py 不再托管静态，只提 /api/v1；
#      dist 由本脚本投放到 WEB_ROOT 交 Nginx 托管，Nginx 占对外端口并反代 /api/v1 到后端回环端口。
#
# 用法（root）：
#   bash deploy/install_server.sh [源码目录]
# 可覆盖变量：APP_USER APP_DIR APP_PORT BACKEND_PORT WEB_ROOT ENABLE_NGINX PIP_INDEX PIP_HOST SRC_DIR
#   APP_PORT：对外入口端口（Nginx listen，园区网唯一放行口，默认 18000）
#   BACKEND_PORT：后端 uvicorn 回环监听端口（须与 .env 的 API_PORT 一致，默认 18010）
#   WEB_ROOT：前端 dist 投放目录（Nginx root）；ENABLE_NGINX=0 时仅装后端 API（需自行前置）
#   离线（园区网）二选一，都不给则维持在线 uv sync：
#     OFFLINE_VENV_TGZ=/path/qcagent-venv.tar.gz   主路径：验证机整 venv 迁移
#     OFFLINE_WHEELS_DIR=/path/wheels               备灾：venv pip --no-index + requirements-offline.txt
#   离线制品准备见 deploy/园区网离线部署Runbook.md（make_offline_bundle.sh 产出）。
# 幂等：可重复执行（代码 rsync 刷新、venv 增量安装、.env 已存在则不覆盖）。
# ============================================================================
set -euo pipefail

APP_USER="${APP_USER:-qcagent}"
APP_DIR="${APP_DIR:-/opt/agent/vision-agent/quality-agent}"   # 规范安装路径；venv 迁移要求验证机与目标机此路径一字不差
APP_PORT="${APP_PORT:-18000}"   # 对外入口端口：Nginx listen（园区网本智能体段 18000–18099；高位端口免提权）
BACKEND_PORT="${BACKEND_PORT:-18010}"   # 后端 uvicorn 只听回环 127.0.0.1 此端口（须与 .env API_PORT 一致）
WEB_ROOT="${WEB_ROOT:-/opt/agent/vision-agent/web}"   # 前端 dist 投放目录（Nginx root）
ENABLE_NGINX="${ENABLE_NGINX:-1}"   # 1=装配 Nginx 站点并投放前端；0=仅装后端 API（目标机无 nginx 或自行前置时）
SRC_DIR="${1:-${SRC_DIR:-/root/QualityCheckingAgent}}"
# ECS 内网 PyPI 源（部署手册 §12.4，实测 ~600MB/s）；公网机器改 https://mirrors.aliyun.com/pypi/simple/
PIP_INDEX="${PIP_INDEX:-https://mirrors.cloud.aliyuncs.com/pypi/simple/}"
PIP_HOST="${PIP_HOST:-mirrors.cloud.aliyuncs.com}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log(){ printf '[install] %s\n' "$*"; }
die(){ printf '[install][ERROR] %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请以 root 运行"
command -v python3.11 >/dev/null 2>&1 || die "未找到 python3.11：先执行 deploy/env-prep/install_python311.sh"
command -v rsync >/dev/null 2>&1 || die "缺少 rsync：yum install -y rsync"
[[ -f "$SRC_DIR/pyproject.toml" ]] || die "源码目录无效：$SRC_DIR（用第一参数或 SRC_DIR= 指定）"
if [[ "$ENABLE_NGINX" == "1" ]]; then
  [[ -d "$SRC_DIR/frontend/dist" ]] || die "$SRC_DIR/frontend/dist 不存在：前后端分离需前端静态产物（应为 make_offline_bundle.sh 植入的顶层 Vue3 dist；未走 bundle 则本地 npm run build 顶层 frontend/ 后把 dist 拷至此）"
fi

log "1/6 创建平台账户 $APP_USER（普通账户，无 sudo）"
id -u "$APP_USER" >/dev/null 2>&1 || useradd -m -s /bin/bash "$APP_USER"
gpasswd -d "$APP_USER" wheel >/dev/null 2>&1 || true

log "2/6 同步代码 $SRC_DIR → $APP_DIR"
mkdir -p "$APP_DIR"
# .env 仅存在于服务器侧，必须排除，否则 --delete 会把它删掉
rsync -a --delete \
  --exclude '.venv/' --exclude '.git/' --exclude '.env' \
  --exclude 'data/' --exclude 'logs/' \
  --exclude 'frontend/node_modules/' --exclude 'frontend/test-results/' \
  --exclude '.pytest_cache/' --exclude '__pycache__/' \
  --exclude '.pw-browsers/' --exclude '.langgraph_api/' \
  "$SRC_DIR"/ "$APP_DIR"/
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

log "3/6 Python 3.11 运行环境安装"
# 三分支（园区网无互联网，ECS 内网 PyPI 镜像不可达）：
#   A) OFFLINE_VENV_TGZ：验证机全量测试通过的整 venv 迁移（主路径，要求目标机
#      解释器路径一致 /usr/local/bin/python3.11 且 glibc 同代；不同代会 import 崩，
#      用分支尾部的关键模块自检兜底捕获）；
#   B) OFFLINE_WHEELS_DIR：venv pip --no-index --find-links（备灾，需随包带
#      requirements-offline.txt —— uv export --frozen 导出的精确锁定清单）；
#   C) 默认在线：uv sync --frozen（ECS 内网镜像，公网验证环境回归不受影响）。
if [[ -n "${OFFLINE_VENV_TGZ:-}" ]]; then
  [[ -f "$OFFLINE_VENV_TGZ" ]] || die "OFFLINE_VENV_TGZ 不存在：$OFFLINE_VENV_TGZ"
  STAGING="$(mktemp -d /tmp/qcvenv.XXXX)"
  log "    [离线A] 解包整 venv：$OFFLINE_VENV_TGZ"
  tar -xzf "$OFFLINE_VENV_TGZ" -C "$STAGING"
  [[ -x "$STAGING/.venv/bin/python" ]] || die "制品包内未找到 .venv/bin/python（打包须以 venv 父目录为 -C 根、顶层名为 .venv）"
  rm -rf "$APP_DIR/.venv"
  mv "$STAGING/.venv" "$APP_DIR/.venv"
  rmdir "$STAGING" 2>/dev/null || true
  chown -R "$APP_USER:$APP_USER" "$APP_DIR/.venv"
elif [[ -n "${OFFLINE_WHEELS_DIR:-}" ]]; then
  [[ -d "$OFFLINE_WHEELS_DIR" ]] || die "OFFLINE_WHEELS_DIR 不存在：$OFFLINE_WHEELS_DIR"
  REQ_OFF="${OFFLINE_REQUIREMENTS:-$APP_DIR/requirements-offline.txt}"
  [[ -f "$REQ_OFF" ]] || die "缺少精确锁定清单 $REQ_OFF（有网机器用 uv export --frozen --no-dev --no-hashes 生成后随 wheels/ 带入）"
  log "    [离线B] venv pip --no-index 安装（wheels：$OFFLINE_WHEELS_DIR）"
  rm -rf "$APP_DIR/.venv"
  runuser -l "$APP_USER" -c \
    "/usr/local/bin/python3.11 -m venv '$APP_DIR/.venv' && \
     '$APP_DIR/.venv/bin/pip' install --no-index --find-links '$OFFLINE_WHEELS_DIR' -r '$REQ_OFF'"
else
  # 2026-09-14 NFS3.1 实跑教训：glibc 2.17 + gcc 4.8.5 下 pip 自由解析会选中
  # 只发 manylinux_2_28 wheel 的新版（tiktoken/pillow/greenlet），回退 sdist 源码编译必败。
  # 改用 uv.lock 锁定版本（= 本地全量测试通过的同一环境）。
  # uv 本体装在独立工具 venv（放项目 .venv 里会被 sync 清掉）。
  UV_TOOL_DIR="${UV_TOOL_DIR:-/opt/uv-tool}"
  if [[ ! -x "$UV_TOOL_DIR/bin/uv" ]]; then
    python3.11 -m venv "$UV_TOOL_DIR"
    "$UV_TOOL_DIR/bin/pip" install -q uv -i "$PIP_INDEX" --trusted-host "$PIP_HOST"
  fi
  [[ -f "$APP_DIR/uv.lock" ]] || die "缺少 uv.lock：请本地 uv lock 生成后随源码上传"
  # --allow-insecure-host：内网镜像 mirrors.cloud.aliyuncs.com 的证书签发给
  # mirrors-ssl.aliyuncs.com/mirrors.aliyun.com（名称不匹配），pip 时代用
  # --trusted-host 容忍；uv 默认严格 TLS，需显式放行（ECS 内网链路，风险可控）。
  log "    [在线C] uv sync --frozen（镜像：$PIP_INDEX）"
  runuser -l "$APP_USER" -c \
    "cd '$APP_DIR' && UV_DEFAULT_INDEX='$PIP_INDEX' UV_HTTP_TIMEOUT=120 '$UV_TOOL_DIR/bin/uv' sync --frozen --python /usr/local/bin/python3.11 --allow-insecure-host '$PIP_HOST'"
fi
# 统一验收：关键编译/纯 py 模块导入（离线 A/B 路径 glibc 不同代、缺轮子都在这里现形）
runuser -l "$APP_USER" -c \
  "'$APP_DIR/.venv/bin/python' -c 'import fastapi, sqlalchemy, aiomysql, langgraph, uvicorn, tiktoken, PIL, greenlet; print(\"[install] venv 自检通过：\" + __import__(\"sys\").version)'" \
  || die "venv 自检失败：离线包与目标机不兼容（glibc 代际/解释器路径/缺 wheel），对照 Runbook 风险表处理"

log "4/6 .env（不存在则从模板放置；启动前必须编辑占位值）"
if [[ ! -f "$APP_DIR/.env" ]]; then
  install -m 600 -o "$APP_USER" -g "$APP_USER" "$SCRIPT_DIR/env.server.example" "$APP_DIR/.env"
  log "    → 请编辑 $APP_DIR/.env：DATABASE_URL 密码 / RAGFLOW_API_KEY / MINIO 凭据"
  log "    → 园区网（跨机接入 RAGFlow/MySQL/模型网关）建议改用 deploy/env.园区网.example 作底稿"
else
  chown "$APP_USER:$APP_USER" "$APP_DIR/.env"; chmod 600 "$APP_DIR/.env"
fi
mkdir -p "$APP_DIR/data" "$APP_DIR/logs"
chown -R "$APP_USER:$APP_USER" "$APP_DIR/data" "$APP_DIR/logs"

log "5/6 systemd 单元 quality-agent.service（后端只听回环 $BACKEND_PORT）"
sed -e "s|__APP_USER__|$APP_USER|g" -e "s|__APP_DIR__|$APP_DIR|g" -e "s|__BACKEND_PORT__|$BACKEND_PORT|g" \
  "$SCRIPT_DIR/systemd/quality-agent.service" > /etc/systemd/system/quality-agent.service
# 前后端分离后，后端 uvicorn 只绑 127.0.0.1:$BACKEND_PORT（回环高端口）：
#   非 root 直接可绑 ≥1024 端口，无需 setcap/AmbientCapabilities 提权（旧 ECS 低端口直出分枝已废除）。
#   对外端口 $APP_PORT 由 Nginx（以 root 跑 master）监听并反代，也不存在低端口提权问题。
systemctl daemon-reload
systemctl enable quality-agent >/dev/null 2>&1 || true

# 5b) Nginx 站点（前后端分离对外入口：托管前端 dist + 反代 /api/v1）
if [[ "$ENABLE_NGINX" == "1" ]]; then
  log "5b Nginx 站点：对外 $APP_PORT，反代 127.0.0.1:$BACKEND_PORT，前端根 $WEB_ROOT"
  command -v nginx >/dev/null 2>&1 || die "未找到 nginx：目标机需预装 nginx（yum install -y nginx）或置 ENABLE_NGINX=0 仅装后端"
  # 投放前端 dist 到 Nginx root（从已同步到 APP_DIR 的构建产物拷入）
  mkdir -p "$WEB_ROOT"
  rsync -a --delete "$APP_DIR/frontend/dist/" "$WEB_ROOT/"
  # Nginx 静态进程需读得 WEB_ROOT；默认 nginx 用户（属主保留 root，目录 755）
  chmod -R a+rX "$WEB_ROOT"
  # 渲染站点配置（端口/后端端口/web 根）
  NGINX_SITE="/etc/nginx/conf.d/quality-agent.conf"
  sed -e "s|__NGINX_PORT__|$APP_PORT|g" -e "s|__BACKEND_PORT__|$BACKEND_PORT|g" -e "s|__WEB_ROOT__|$WEB_ROOT|g" \
    "$SCRIPT_DIR/nginx/quality-agent.conf.template" > "$NGINX_SITE"
  nginx -t || die "nginx 配置校验失败：检查 $NGINX_SITE 与 WEB_ROOT=$WEB_ROOT"
  systemctl enable nginx >/dev/null 2>&1 || true
  systemctl reload nginx 2>/dev/null || nginx -s reload 2>/dev/null || systemctl start nginx
  log "    Nginx 已装配并生效（$NGINX_SITE）；前端根 $WEB_ROOT 已就位"
fi

log "6/6 安装完成（未启动 —— 先完成 .env 与数据库准备）"
cat <<EOF

后续步骤：
  1) 建库 + 专用账号（在 RAGFlow 的 MySQL 实例执行；先改 create_platform_user.sql 里的 <CHANGE_ME>）：
     docker exec -i docker-mysql-1 mysql -uroot -p'<root密码>' < $APP_DIR/scripts/sql/init.sql
     docker exec -i docker-mysql-1 mysql -uroot -p'<root密码>' < $APP_DIR/deploy/sql/create_platform_user.sql
  2) 编辑配置：vim $APP_DIR/.env
  3) 能力注册（种子；--no-tasks 跳过演示图片播种）：
     cd $APP_DIR && runuser -u $APP_USER -- .venv/bin/python -m scripts.sync_capabilities --no-tasks
     随后按 deploy/README.md「能力端点切换」把各能力 endpoint 从 mock 改到 1810X。
  4) 启动并验证：
     systemctl start quality-agent && journalctl -u quality-agent -f
     # 后端回环直连（健康探针）：
     curl -s http://127.0.0.1:$BACKEND_PORT/api/v1/health
     # 经 Nginx 对外入口（分离模态下用户访问的端口；仅 ENABLE_NGINX=1 时有义）：
     curl -s -o /dev/null -w '前端首页 HTTP %{http_code}\n' http://127.0.0.1:$APP_PORT/
     curl -s http://127.0.0.1:$APP_PORT/api/v1/health   # 经反代的 API
EOF
