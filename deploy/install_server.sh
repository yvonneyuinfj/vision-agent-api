#!/usr/bin/env bash
# ============================================================================
# 智能质检 Agent 平台 —— 服务器非容器化安装脚本（B′）
#
# 前提：
#   1) deploy/env-prep/install_python311.sh 已完成（python3.11 可用）；
#   2) RAGFlow Docker 套件运行中（MySQL localhost:3306 / MinIO localhost:9000，
#      容器名 docker-mysql-1 / docker-minio-1，见部署手册 §10）；
#   3) 源码（含前端静态产物 frontend/dist，dist 不入 git）已上传到 SRC_DIR；
#      该 dist 应为 make_offline_bundle.sh 构建并植入的**顶层 Vue3 深蓝版**产物
#      （后端 app/main.py 硬编码同源托管 quality-agent/frontend/dist）。
#
# 用法（root）：
#   bash deploy/install_server.sh [源码目录]
# 可覆盖变量：APP_USER APP_DIR APP_PORT PIP_INDEX PIP_HOST SRC_DIR
#   离线（园区网）二选一，都不给则维持在线 uv sync：
#     OFFLINE_VENV_TGZ=/path/qcagent-venv.tar.gz   主路径：验证机整 venv 迁移
#     OFFLINE_WHEELS_DIR=/path/wheels               备灾：venv pip --no-index + requirements-offline.txt
#   离线制品准备见 deploy/园区网离线部署Runbook.md（make_offline_bundle.sh 产出）。
# 幂等：可重复执行（代码 rsync 刷新、venv 增量安装、.env 已存在则不覆盖）。
# ============================================================================
set -euo pipefail

APP_USER="${APP_USER:-qcagent}"
APP_DIR="${APP_DIR:-/opt/agent/vision-agent/quality-agent}"   # 规范安装路径；venv 迁移要求验证机与目标机此路径一字不差
APP_PORT="${APP_PORT:-80}"   # ECS 验证环境缺省 80（低端口需提权）；园区网传 APP_PORT=18000（高位端口免提权，须与 .env 的 API_PORT 一致）
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
[[ -d "$SRC_DIR/frontend/dist" ]] || log "警告：$SRC_DIR/frontend/dist 不存在，平台首页将无前端静态产物（应为 make_offline_bundle.sh 构建并植入的顶层 Vue3 深蓝版 dist；未走 bundle 则本地 npm run build 顶层 frontend/ 后将 dist 拷至此处再上传）"

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

log "5/6 systemd 单元 quality-agent.service"
sed -e "s|__APP_USER__|$APP_USER|g" -e "s|__APP_DIR__|$APP_DIR|g" -e "s|__APP_PORT__|$APP_PORT|g" \
  "$SCRIPT_DIR/systemd/quality-agent.service" > /etc/systemd/system/quality-agent.service
# 端口分两类：<1024 为特权端口，非 root 需提权；>=1024 非 root 可直接绑定（免提权）。
#   园区网本智能体端口段 18000–18099（APP_PORT=18000）为高位端口 → 走免提权快路径；
#   公网验证环境 ECS 沿用 80（低端口）→ 进入下面的 systemd 版本分支。
# 低端口提权分两条路（2026-09-14 NFS 3.1 / systemd 219 实跑教训）：
#   systemd >= 229：单元里的 AmbientCapabilities 原生生效，NoNewPrivileges 加固保留；
#   systemd <  229：AmbientCapabilities 是 Unknown lvalue 被忽略，而 NoNewPrivileges
#                  会让内核忽略 setcap 文件能力 → 非 root 绑低端口必败（Errno 13）。
#                  解法：给真实解释器 setcap cap_net_bind_service，并注释渲染后单元的
#                  NoNewPrivileges（CapabilityBoundingSet 仍把能力上限锁死为仅低端口
#                  绑定）。安全权衡与差异项记录见 deploy/README「systemd 版本差异」。
#                  注意：setcap 作用于共享的 /usr/local/bin/python3.11，本机所有用
#                  该解释器的账户进程都将可绑 <1024 端口（受控内部账户，已评估接受）。
if [[ "$APP_PORT" -ge 1024 ]]; then
  log "    APP_PORT=$APP_PORT 为高位端口（≥1024）：非 root 直接可绑，免 setcap/AmbientCapabilities 提权"
else
  SYSTEMD_VER="$(systemctl --version | head -1 | awk '{print $2}')"
  if [[ "${SYSTEMD_VER:-0}" -lt 229 ]]; then
    command -v setcap >/dev/null 2>&1 || die "缺少 setcap：yum install -y libcap"
    PY_REAL="$(readlink -f "$APP_DIR/.venv/bin/python")"
    [[ -n "$PY_REAL" && -x "$PY_REAL" ]] || die "无法定位 venv 真实解释器：$PY_REAL"
    setcap 'cap_net_bind_service=+ep' "$PY_REAL"
    sed -i 's|^NoNewPrivileges=true|#NoNewPrivileges=true  # systemd <229：nnp 会使 setcap 文件能力失效，装机时自动注释（见 deploy/README）|' \
      /etc/systemd/system/quality-agent.service
    log "    低端口 + systemd $SYSTEMD_VER < 229：setcap → $PY_REAL，已注释 NoNewPrivileges"
  else
    log "    低端口 + systemd $SYSTEMD_VER >= 229：AmbientCapabilities 原生生效，保留 NoNewPrivileges"
  fi
fi
systemctl daemon-reload
systemctl enable quality-agent >/dev/null 2>&1 || true

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
     curl -s http://127.0.0.1:$APP_PORT/api/v1/health
EOF
