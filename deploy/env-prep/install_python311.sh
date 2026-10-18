#!/usr/bin/env bash
# ============================================================================
# Python 3.11 源码编译安装（中科方德服务器 · 验证环境准备）
#
# 背景：
#   - NFS/NFC Server 为 yum 系（无 dnf/apt），系统仓库无 Python 3.11 → 源码编译；
#   - 3.10 内核一代 OS（CentOS7 同代）自带 OpenSSL 1.0.2，而 Python 3.11 的
#     ssl 模块要求 OpenSSL >= 1.1.1 —— 脚本会先探测，必要时先编译 OpenSSL 1.1.1w
#     到独立前缀（不动系统 openssl，符合「基线冻结」对全局组件的要求）。
#
# 用法（root）：bash deploy/env-prep/install_python311.sh
# 日志：/var/log/install_python311.log
# 可覆盖变量：
#   PY_VERSION=3.11.14          # Python 版本
#   OPENSSL_VERSION=1.1.1w      # 需要自建时的 OpenSSL 版本
#   OPENSSL_PREFIX=/usr/local/openssl-1.1.1
#   PY_TARBALL=/path/Python-3.11.14.tar.xz      # 预下载包（离线/内网机器）
#   OPENSSL_TARBALL=/path/openssl-1.1.1w.tar.gz # 同上
#   OFFLINE_DEPS=1            # 1 = 跳过 yum 装依赖（园区网离线机，依赖已用 RPM 预装）
#   ENABLE_OPTIMIZATIONS=0      # 1 = PGO 构建（慢 5-10 倍，验证环境不建议）
# ============================================================================
set -euo pipefail

PY_VERSION="${PY_VERSION:-3.11.14}"
OPENSSL_VERSION="${OPENSSL_VERSION:-1.1.1w}"
OPENSSL_PREFIX="${OPENSSL_PREFIX:-/usr/local/openssl-1.1.1}"
WORKDIR="${WORKDIR:-/usr/local/src/py311-build}"
LOG_FILE="/var/log/install_python311.log"
ENABLE_OPTIMIZATIONS="${ENABLE_OPTIMIZATIONS:-0}"

[[ $EUID -eq 0 ]] || { echo "请以 root 运行" >&2; exit 1; }
mkdir -p "$WORKDIR"
exec > >(tee -a "$LOG_FILE") 2>&1

log(){ printf '\n[py311] %s\n' "$*"; }
die(){ printf '[py311][ERROR] %s\n' "$*" >&2; exit 1; }
# set -e 静默退出难排查：ERR trap 打出行号（2026-09-14 实跑教训）
trap 'printf "\n[py311][ERROR] 脚本在第 %s 行意外退出（status=%s）\n" "$LINENO" "$?" >&2' ERR

# 依次尝试多个下载源（服务器网络可达性见部署手册 §0.2：objects.githubusercontent 不通，
# codeload.github / npmmirror / huaweicloud 可达）。
# 2026-09-14 实测教训：huaweicloud /openssl/ 路径返回 HTML 错误页（HTTP 200，~12KB），
# curl -f 拦不住 → 下载后必须做压缩包完整性校验，坏档视为该源失败并删除换源。
verify_archive(){ # 按目标文件名后缀校验 gzip/xz 完整性（容忍 .part 中间名）
  local f="$1"
  [[ -s "$f" ]] || return 1
  case "$f" in
    *.tar.gz*|*.tgz*) gzip -t "$f" >/dev/null 2>&1 ;;
    *.tar.xz*)        xz -t "$f" >/dev/null 2>&1 ;;
    *)                return 0 ;;
  esac
}
fetch(){ # fetch <输出文件> <url1> [url2 ...]
  local out="$1"; shift
  if [[ -s "$out" ]]; then
    if verify_archive "$out"; then log "已存在且校验通过：$out（跳过下载）"; return 0; fi
    log "已存在但校验失败（疑似错误页），删除重下：$out"; rm -f "$out"
  fi
  local url
  for url in "$@"; do
    log "下载：$url"
    if curl -fL --retry 2 --connect-timeout 10 --max-time 900 -o "$out.part" "$url" \
       && verify_archive "$out.part"; then
      mv "$out.part" "$out"; return 0
    fi
    log "  ↳ 该源失败或文件损坏，换下一个源"
    rm -f "$out.part"
  done
  return 1
}

log "0/5 安装编译依赖（yum）"
if [[ "${OFFLINE_DEPS:-0}" == 1 ]]; then
  log "  OFFLINE_DEPS=1 → 跳过 yum（请先确认已用离线 RPM 预装：gcc gcc-c++ make zlib-devel openssl-devel libffi-devel xz-devel readline-devel sqlite-devel bzip2-devel ncurses-devel gdbm-devel libuuid-devel；缺任何一个都会导致对应模块编不出来）"
else
  yum install -y gcc gcc-c++ make wget curl xz tar patch perl-Core \
    zlib-devel bzip2-devel openssl-devel ncurses-devel sqlite-devel \
    readline-devel tk-devel gdbm-devel libffi-devel xz-devel \
    || log "警告：部分依赖安装失败，继续（若编译报错请对照上方 yum 输出补装）"
fi

log "1/5 探测系统 OpenSSL（Python 3.11 要求 >= 1.1.1）"
SYS_OPENSSL="$(openssl version 2>/dev/null | awk '{print $2}' || echo none)"
log "系统 OpenSSL：$SYS_OPENSSL"
case "$SYS_OPENSSL" in
  1.1.1*|3.*)  NEED_OPENSSL=0; OPENSSL_ARG="" ;;
  # rpath 用显式路径而非 auto（auto 依赖 pkg-config 能发现非标准前缀，2026-09-14 实测教训）
  *)           NEED_OPENSSL=1; OPENSSL_ARG="--with-openssl=$OPENSSL_PREFIX --with-openssl-rpath=$OPENSSL_PREFIX/lib" ;;
esac

if [[ "$NEED_OPENSSL" == 1 ]]; then
  # 幂等：已编译且自检通过则跳过（自检需 LD_LIBRARY_PATH：新装 openssl 二进制自身未嵌 rpath）
  if LD_LIBRARY_PATH="$OPENSSL_PREFIX/lib" "$OPENSSL_PREFIX/bin/openssl" version >/dev/null 2>&1; then
    log "2/5 OpenSSL 已就位：$(LD_LIBRARY_PATH="$OPENSSL_PREFIX/lib" "$OPENSSL_PREFIX/bin/openssl" version 2>&1)（跳过编译）"
  else
  log "2/5 源码编译 OpenSSL $OPENSSL_VERSION → $OPENSSL_PREFIX（独立前缀，不覆盖系统库）"
  if [[ -n "${OPENSSL_TARBALL:-}" && -s "$OPENSSL_TARBALL" ]]; then
    cp "$OPENSSL_TARBALL" "$WORKDIR/"
  else
    fetch "$WORKDIR/openssl-$OPENSSL_VERSION.tar.gz" \
      "https://codeload.github.com/openssl/openssl/tar.gz/refs/tags/OpenSSL_1_1_1${OPENSSL_VERSION##1.1.1}" \
      "https://www.openssl.org/source/openssl-$OPENSSL_VERSION.tar.gz" \
      "https://ghfast.top/https://github.com/openssl/openssl/releases/download/OpenSSL_1_1_1${OPENSSL_VERSION##1.1.1}/openssl-$OPENSSL_VERSION.tar.gz" \
      "https://mirrors.huaweicloud.com/openssl/openssl-$OPENSSL_VERSION.tar.gz" \
      || die "OpenSSL 源码下载失败：请手工下载后以 OPENSSL_TARBALL=/path 重跑"
  fi
  tar -xzf "$WORKDIR/openssl-$OPENSSL_VERSION.tar.gz" -C "$WORKDIR"
  # codeload 等源的顶层目录名与发行包不同，动态探测解压目录。
  # 管道内 `|| true`：head 提前退出会使 tar 收 SIGPIPE(141)，pipefail+set -e 会静默杀脚本
  #（2026-09-14 实跑故障点），head 已输出首行故取值不受影响。
  SSL_SRC_DIR="$(tar -tzf "$WORKDIR/openssl-$OPENSSL_VERSION.tar.gz" | head -1 | cut -d/ -f1 || true)"
  [[ -n "$SSL_SRC_DIR" && -d "$WORKDIR/$SSL_SRC_DIR" ]] || die "OpenSSL 解压目录探测失败：'$SSL_SRC_DIR'"
  log "  开始编译 OpenSSL（config + make -j$(nproc)，预计 3~6 分钟）"
  ( cd "$WORKDIR/$SSL_SRC_DIR" \
    && ./config --prefix="$OPENSSL_PREFIX" --openssldir="$OPENSSL_PREFIX" shared \
    && make -j"$(nproc)" \
    && make install_sw )
    LD_LIBRARY_PATH="$OPENSSL_PREFIX/lib" "$OPENSSL_PREFIX/bin/openssl" version \
      || die "OpenSSL 已安装但自检失败"
  fi
else
  log "2/5 系统 OpenSSL 满足要求，跳过自建"
fi

log "3/5 下载 Python $PY_VERSION 源码"
if [[ -n "${PY_TARBALL:-}" && -s "$PY_TARBALL" ]]; then
  cp "$PY_TARBALL" "$WORKDIR/"
else
  fetch "$WORKDIR/Python-$PY_VERSION.tar.xz" \
    "https://registry.npmmirror.com/-/binary/python/$PY_VERSION/Python-$PY_VERSION.tar.xz" \
    "https://mirrors.huaweicloud.com/python/$PY_VERSION/Python-$PY_VERSION.tar.xz" \
    "https://www.python.org/ftp/python/$PY_VERSION/Python-$PY_VERSION.tar.xz" \
    || die "Python 源码下载失败：请手工下载后以 PY_TARBALL=/path 重跑"
fi

log "4/5 编译安装（make altinstall → /usr/local/bin/python3.11，不触碰系统 python）"
tar -xJf "$WORKDIR/Python-$PY_VERSION.tar.xz" -C "$WORKDIR"
PY_SRC_DIR="$(tar -tJf "$WORKDIR/Python-$PY_VERSION.tar.xz" | head -1 | cut -d/ -f1 || true)"
[[ -n "$PY_SRC_DIR" && -d "$WORKDIR/$PY_SRC_DIR" ]] || die "Python 解压目录探测失败：'$PY_SRC_DIR'"
log "  开始编译 CPython（configure + make -j$(nproc)，预计 5~10 分钟）"
# 让 pkg-config 能发现自建前缀的 openssl（configure 探测需要；显式 rpath 之外的保险）
if [[ "$NEED_OPENSSL" == 1 ]]; then
  export PKG_CONFIG_PATH="$OPENSSL_PREFIX/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
fi
OPT_ARG=""
[[ "$ENABLE_OPTIMIZATIONS" == 1 ]] && OPT_ARG="--enable-optimizations"
( cd "$WORKDIR/$PY_SRC_DIR" \
  && ./configure --prefix=/usr/local $OPT_ARG $OPENSSL_ARG \
  && make -j"$(nproc)" \
  && make altinstall )

log "5/5 验证（对应验证文档基线命令与验收项 2）"
/usr/local/bin/python3.11 --version || die "python3.11 未安装成功"
/usr/local/bin/python3.11 -m pip --version || die "pip 不可用"
/usr/local/bin/python3.11 - <<'PYEOF'
import ssl, sqlite3, ctypes, zlib, bz2, lzma, readline
print("[py311] ssl:", ssl.OPENSSL_VERSION)
print("[py311] 关键标准库 import 全部通过")
PYEOF
rm -rf /tmp/venv-acceptance
/usr/local/bin/python3.11 -m venv /tmp/venv-acceptance
/tmp/venv-acceptance/bin/python --version
/tmp/venv-acceptance/bin/pip --version >/dev/null && log "venv + pip 验证通过"
rm -rf /tmp/venv-acceptance

cat <<EOF

[py311] 安装完成。
  - 解释器：/usr/local/bin/python3.11（$(/usr/local/bin/python3.11 --version 2>&1)）
  - pip 源建议（ECS 内网实测 ~600MB/s，公网 ~244kB/s，见部署手册 §12.4）：
      python3.11 -m pip config set global.index-url https://mirrors.cloud.aliyuncs.com/pypi/simple/
      python3.11 -m pip config set global.trusted-host mirrors.cloud.aliyuncs.com
  - 本脚本可重复执行（增量）；基线冻结后重跑属环境变更，需牵头单位确认（验证文档 §八）。
EOF
