#!/usr/bin/env bash
# ============================================================================
# 环境基线采集（《公网云服务器验证环境准备》第二章 + 验收项 7）
#
# 生成 Markdown 基线记录：OS/内核/CPU/内存/磁盘/关键运行库/Python 3.11/网络与访问。
# 用法：bash deploy/env-prep/collect_baseline.sh
#   输出：./baseline_<主机名>_<日期>.md（OUT_FILE 可覆盖；root 执行信息更全）
# 说明：本脚本只读不写系统（除输出文件），基线冻结后仍可重复执行用于比对。
# ============================================================================
set -uo pipefail   # 不用 -e：单条探测失败不中断整体采集

OUT="${OUT_FILE:-baseline_$(hostname -s 2>/dev/null || echo host)_$(date +%Y%m%d).md}"

have(){ command -v "$1" >/dev/null 2>&1; }
run(){ # run <命令...>：带命令回显的代码块，失败不中断
  printf '\n```console\n$ %s\n' "$*"
  "$@" 2>&1 || printf '(命令不存在或执行失败)\n'
  printf '```\n'
}
block(){ # block <标题> <内容>
  printf '\n```console\n# %s\n%s\n```\n' "$1" "$2"
}

{
printf '# 环境基线记录\n\n'
printf -- '- 采集时间：%s\n' "$(date '+%F %T %Z')"
printf -- '- 采集主机：%s（%s）\n' "$(hostname 2>/dev/null || echo unknown)" "$(uname -n 2>/dev/null)"
printf -- '- 采集人：%s\n' "${SUDO_USER:-$(whoami)}"
printf -- '- 依据：《公网云服务器验证环境准备》第二章（表1 及交付前确认清单）\n'

printf '\n## 1. 操作系统与内核\n'
run uname -a
run uname -m
run cat /etc/os-release
have rpm && run rpm -q kernel

printf '\n## 2. CPU（验收关注：型号 / 物理核-逻辑核 / 指令集）\n'
have lscpu && run lscpu
printf '\n```console\n$ 指令集摘要（SSE4/AVX 系）\n'
if have lscpu; then
  lscpu 2>/dev/null | tr ' ,' '\n\n' | grep -E '^(sse4_1|sse4_2|avx|avx2|avx512f|avx512bw|avx512vl|fma|f16c|amx[a-z]*)$' | sort -u
else
  grep -o -E '(sse4_2|avx2|avx512f|avx|fma|f16c)' /proc/cpuinfo 2>/dev/null | sort -u
fi
printf '```\n'

printf '\n## 3. 内存与磁盘（验收关注：总量 / /opt 可用空间）\n'
run free -h
run df -h
printf '\n```console\n$ /opt 可用空间\n'
df -h /opt 2>/dev/null || df -h /
printf '```\n'

printf '\n## 4. 关键系统运行库（glibc / gcc / libstdc++ / OpenSSL）\n'
run ldd --version
run gcc --version
run g++ --version
have rpm && run rpm -q glibc libstdc++ gcc gcc-c++ openssl
run openssl version
if have strings && [[ -f /usr/lib64/libstdc++.so.6 ]]; then
  block "libstdc++ GLIBCXX 上限" "$(strings /usr/lib64/libstdc++.so.6 | grep '^GLIBCXX_' | sort -V | tail -3)"
fi

printf '\n## 5. Python 3.11（验收项 2：解释器 / pip / venv）\n'
if have python3.11; then
  run python3.11 --version
  run python3.11 -m pip --version
  printf '\n```console\n$ python3.11 -m venv --help >/dev/null && echo venv-ok\n'
  python3.11 -m venv --help >/dev/null 2>&1 && echo venv-ok || echo venv-FAIL
  printf '```\n'
else
  printf '\n**python3.11 未安装**（先执行 deploy/env-prep/install_python311.sh）\n'
fi
printf '\n```console\n$ 系统自带 python（仅记录，不作基线）\n'
python3 --version 2>&1 || echo '(无 python3)'
printf '```\n'

printf '\n## 6. 网络与访问（IP / SSH 端口 / 防火墙 / SELinux）\n'
printf '\n```console\n$ hostname -I\n'
hostname -I 2>/dev/null || ip -4 addr show 2>/dev/null | grep inet
printf '```\n'
printf '\n```console\n$ SSH 端口\n'
SSH_PORT_LINE="$( (sshd -T 2>/dev/null || /usr/sbin/sshd -T 2>/dev/null) | awk '/^port /{print "Port "$2}' )"
if [[ -n "$SSH_PORT_LINE" ]]; then
  echo "$SSH_PORT_LINE"
else
  grep -Ei '^[[:space:]]*#?[[:space:]]*Port\b' /etc/ssh/sshd_config 2>/dev/null || echo '(无法探测，查 /etc/ssh/sshd_config)'
fi
printf '```\n'
printf '\n```console\n$ firewalld / SELinux\n'
systemctl is-active firewalld 2>/dev/null || echo 'firewalld: inactive/absent'
firewall-cmd --list-all 2>/dev/null || true
getenforce 2>/dev/null || echo 'SELinux: 无法探测'
printf '```\n'

printf '\n## 7. 容器套件现状（RAGFlow 栈，供差异记录）\n'
if have docker; then
  run docker --version
  printf '\n```console\n$ docker ps（运行中容器）\n'
  docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || echo '(docker 不可用或无权限)'
  printf '```\n'
else
  printf '\n未安装 Docker（纯验证机可不装；RAGFlow 套件机应有）。\n'
fi

printf '\n---\n\n'
printf '> 云环境与最终生产环境的差异项（如有）请在下方登记（验证文档 §八）：\n>\n'
printf '> | 差异项 | 云环境实际值 | 生产要求值 | 影响评估 |\n> |---|---|---|---|\n> | （待填） | | | |\n'
} | tee "$OUT"

printf '\n[baseline] 已生成：%s（请归档并提交牵头单位）\n' "$OUT"
