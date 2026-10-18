#!/usr/bin/env bash
# ============================================================================
# 验收自检（《公网云服务器验证环境准备》验收项 1-7）
#
# 用法（root —— 项 4/5 需 su 到工具账户）：
#   bash deploy/env-prep/verify_acceptance.sh
# 可覆盖变量：
#   EXPECT_OS="NFC Server 4.0"   # 验收项 1 的期望 OS（演练机可临时改 "NFS Server 3.1"）
#   ROOT_DIR=/opt/agent/vision-agent/tools
#   TOOLS="xray:18011 ..."       # 与 init_ai_tools_env.sh 相同格式
#   PORT_MODE=free               # free=端口空闲可分配（部署前）；listen=已监听且 /health 可应答（部署后）
#   BASELINE_FILE=               # 基线记录文件路径（默认自动找最新 baseline_*.md）
# 退出码：0=全部通过；1=存在 FAIL。
# ============================================================================
set -uo pipefail

EXPECT_OS="${EXPECT_OS:-NFC Server 4.0}"
ROOT_DIR="${ROOT_DIR:-/opt/agent/vision-agent/tools}"
TOOLS="${TOOLS:-xray:18011 fracture:18012 metallography:18013 borescope:18014 hotsection:18015 fluorescence:18016}"
PORT_MODE="${PORT_MODE:-free}"

PASS=0; FAIL=0; RESULTS=()
ok(){ RESULTS+=("PASS|$1|$2"); PASS=$((PASS+1)); }
ng(){ RESULTS+=("FAIL|$1|$2"); FAIL=$((FAIL+1)); }

[[ $EUID -eq 0 ]] || { echo "请以 root 运行（项 4/5 需要 su 到工具账户）" >&2; exit 1; }

# --- 项 1：操作系统与架构 ---
PRETTY="$( . /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-$NAME $VERSION}" )"
ARCH="$(uname -m)"
if [[ "$PRETTY" == *"$EXPECT_OS"* ]]; then ok "1 操作系统" "$PRETTY（匹配期望 $EXPECT_OS）"
else ng "1 操作系统" "实际 [$PRETTY]，期望包含 [$EXPECT_OS]（演练机差异项，记录后由牵头单位确认）"; fi
[[ "$ARCH" == "x86_64" ]] && ok "1 CPU 架构" "$ARCH" || ng "1 CPU 架构" "$ARCH ≠ x86_64"

# --- 项 2：Python 3.11 ---
if command -v python3.11 >/dev/null 2>&1; then
  PYV="$(python3.11 --version 2>&1)"
  [[ "$PYV" == *" 3.11."* ]] && ok "2 Python" "$PYV" || ng "2 Python" "$PYV 非 3.11.x"
  python3.11 -m pip --version >/dev/null 2>&1 && ok "2 pip" "$(python3.11 -m pip --version 2>&1 | awk '{print $1,$2}')" || ng "2 pip" "python3.11 -m pip 不可用"
  python3.11 -m venv --help >/dev/null 2>&1 && ok "2 venv 模块" "可用" || ng "2 venv 模块" "不可用"
else
  ng "2 Python" "python3.11 未安装（先执行 install_python311.sh）"; ng "2 pip" "依赖 python3.11"; ng "2 venv 模块" "依赖 python3.11"
fi

# --- 项 3：目录 ---
if [[ -d "$ROOT_DIR" ]]; then
  ok "3 根目录" "$ROOT_DIR 存在（$(stat -c '%U:%G %a' "$ROOT_DIR")）"
else
  ng "3 根目录" "$ROOT_DIR 不存在"
fi
for entry in $TOOLS; do
  name="${entry%%:*}"; dir="$ROOT_DIR/$name"
  if [[ -d "$dir" ]]; then
    owner="$(stat -c '%U' "$dir")"
    [[ "$owner" == "$name" ]] && ok "3 目录 $name" "$dir 属主=$owner" || ng "3 目录 $name" "属主=$owner，期望=$name"
  else
    ng "3 目录 $name" "$dir 不存在"
  fi
done

# --- 项 4：账户（可解析、无 sudo、密码锁定）---
for entry in $TOOLS; do
  name="${entry%%:*}"
  if getent passwd "$name" >/dev/null 2>&1; then
    groups="$(id -nG "$name" 2>/dev/null)"
    if [[ " $groups " == *" wheel "* || " $groups " == *" sudo "* ]]; then
      ng "4 账户 $name" "属于特权组：$groups"
    else
      sudo_probe="$(su - "$name" -c 'command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null && echo HAS || echo NO' 2>/dev/null || echo NO)"
      [[ "$sudo_probe" == "NO" ]] && ok "4 账户 $name" "存在、无 sudo、组=[$groups]" || ng "4 账户 $name" "可免密 sudo！"
    fi
  else
    ng "4 账户 $name" "账户不存在"
  fi
done

# --- 项 5：普通账户可在本目录创建并激活 venv（每工具实测）---
for entry in $TOOLS; do
  name="${entry%%:*}"; dir="$ROOT_DIR/$name"
  if [[ -d "$dir" ]] && getent passwd "$name" >/dev/null 2>&1; then
    if su - "$name" -c "rm -rf '$dir/venv-acceptance' && python3.11 -m venv '$dir/venv-acceptance' && '$dir/venv-acceptance/bin/python' --version && rm -rf '$dir/venv-acceptance'" >/dev/null 2>&1; then
      ok "5 venv@$name" "创建/激活/清理 成功"
    else
      ng "5 venv@$name" "venv 创建或运行失败（su - $name 手工复测）"
    fi
  else
    ng "5 venv@$name" "前置缺失（目录或账户）"
  fi
done

# --- 项 6：端口 ---
port_listening(){ ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$1$"; }
for entry in $TOOLS; do
  name="${entry%%:*}"; port="${entry##*:}"
  if [[ "$PORT_MODE" == listen ]]; then
    if port_listening "$port"; then
      body="$(curl -s --max-time 5 "http://127.0.0.1:$port/health" 2>/dev/null | head -c 200)"
      [[ "$body" == *code* || "$body" == *status* ]] && ok "6 端口 $port($name)" "已监听且 /health 可应答" || ng "6 端口 $port($name)" "已监听但 /health 应答异常：${body:-空}"
    else
      ng "6 端口 $port($name)" "未监听（PORT_MODE=listen）"
    fi
  else
    port_listening "$port" && ng "6 端口 $port($name)" "已被占用（期望空闲可分配）" || ok "6 端口 $port($name)" "空闲，可按约定分配"
  fi
done

# --- 项 7：基线记录 ---
BASELINE_FILE="${BASELINE_FILE:-$(ls -1t baseline_*.md "$ROOT_DIR"/baseline_*.md 2>/dev/null | head -1 || true)}"
if [[ -n "$BASELINE_FILE" && -s "$BASELINE_FILE" ]]; then
  ok "7 基线记录" "$BASELINE_FILE（$(wc -l < "$BASELINE_FILE") 行）"
else
  ng "7 基线记录" "未找到 baseline_*.md（先执行 collect_baseline.sh，或 BASELINE_FILE= 指定）"
fi

# --- 汇总 ---
echo
echo "================ 验收自检结果 ================"
for r in "${RESULTS[@]}"; do
  IFS='|' read -r st item detail <<< "$r"
  printf '[%s] %-16s %s\n' "$st" "$item" "$detail"
done
echo "=============================================="
echo "通过 $PASS 项，失败 $FAIL 项。"
[[ "$FAIL" -eq 0 ]] || exit 1
