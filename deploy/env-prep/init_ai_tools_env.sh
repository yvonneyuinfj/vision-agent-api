#!/usr/bin/env bash
# ============================================================================
# 验证环境初始化（《公网云服务器验证环境准备》第三、四章）
#
# 内容：
#   1) /opt/agent/vision-agent/tools/<tool> 六个工具目录（对应表 2：xray/fracture/metallography/
#      borescope/hotsection/fluorescence，端口 18011-18016）；
#   2) 每工具独立登录账户：无 sudo（移出 wheel）、密码锁定（仅 SSH 公钥登录）、
#      仅本工具目录可写（目录 750，属主=账户）；
#   3) firewalld 放行 18011-18016（+ 平台端口 80）；云安全组需另行配置（打印提醒）。
#
# 用法（root）：bash deploy/env-prep/init_ai_tools_env.sh
# 可覆盖变量：TOOLS ROOT_DIR PLATFORM_PORT WITH_PLATFORM_PORT
# 幂等：可重复执行。基线冻结后重跑属环境变更，需牵头单位确认（文档 §八）。
# ============================================================================
set -euo pipefail

TOOLS="${TOOLS:-xray:18011 fracture:18012 metallography:18013 borescope:18014 hotsection:18015 fluorescence:18016}"
ROOT_DIR="${ROOT_DIR:-/opt/agent/vision-agent/tools}"
PLATFORM_PORT="${PLATFORM_PORT:-80}"
WITH_PLATFORM_PORT="${WITH_PLATFORM_PORT:-1}"

log(){ printf '[env-init] %s\n' "$*"; }
warn(){ printf '[env-init][WARN] %s\n' "$*"; }
die(){ printf '[env-init][ERROR] %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请以 root 运行"

log "1/3 根目录 $ROOT_DIR"
mkdir -p "$ROOT_DIR"
chown root:root "$ROOT_DIR"; chmod 755 "$ROOT_DIR"

log "2/3 工具账户 + 工作目录（账户无 sudo、仅本目录可写）"
for entry in $TOOLS; do
  name="${entry%%:*}"; port="${entry##*:}"
  if ! id -u "$name" >/dev/null 2>&1; then
    useradd -m -s /bin/bash "$name"
    log "  创建账户：$name（端口 $port）"
  fi
  gpasswd -d "$name" wheel >/dev/null 2>&1 || true   # 确保不在 wheel（无 sudo）
  usermod -L "$name" 2>/dev/null || true              # 密码保持锁定 → 仅公钥登录
  dir="$ROOT_DIR/$name"
  mkdir -p "$dir"
  chown "$name:$name" "$dir"
  chmod 750 "$dir"                                    # 其他单位账户不可进入
done

log "3/3 防火墙端口"
if systemctl is-active --quiet firewalld 2>/dev/null; then
  for entry in $TOOLS; do
    firewall-cmd --permanent --add-port="${entry##*:}/tcp" >/dev/null
  done
  [[ "$WITH_PLATFORM_PORT" == 1 ]] && firewall-cmd --permanent --add-port="${PLATFORM_PORT}/tcp" >/dev/null
  firewall-cmd --reload
  log "  firewalld 已放行：$(for e in $TOOLS; do printf '%s ' "${e##*:}"; done)${WITH_PLATFORM_PORT:+ $PLATFORM_PORT}"
else
  warn "firewalld 未运行 —— 请人工确认 iptables 规则与云平台安全组"
fi

cat <<EOF

[env-init] 完成。分配表（发给各开发单位）：

| 工具 | 账户 | 目录 | 端口 |
|---|---|---|---|
$(for entry in $TOOLS; do printf '| %s | %s | %s/%s | %s |\n' "${entry%%:*}" "${entry%%:*}" "$ROOT_DIR" "${entry%%:*}" "${entry##*:}"; done)

后续人工步骤：
  1) 各单位提交 SSH 公钥后放置（禁止密码登录）：
       install -d -m 700 -o <账户> -g <账户> /home/<账户>/.ssh
       install -m 600 -o <账户> -g <账户> <公钥文件> /home/<账户>/.ssh/authorized_keys
  2) 云平台安全组：18011-18016 仅对联调来源 IP/内网开放；$PLATFORM_PORT 按需开放；22 限源。
  3) 各工具目录内 app/models/config/logs/venv 由开发单位自建（文档第三章）。
  4) 验收前执行 deploy/env-prep/verify_acceptance.sh 自检。
EOF
