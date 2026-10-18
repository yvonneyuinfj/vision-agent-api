#!/usr/bin/env bash
# ============================================================================
# 园区网离线部署制品包（bundle）一键准备
#
# 产出目录（默认 ./offline-bundle，整体拷入 U 盘带进园区网）：
#   source.tar.gz               平台源码 + frontend/dist（已植入顶层 Vue3 前端产物）+ uv.lock + SQL/脚本（不含 .env）
#   requirements-offline.txt    uv.lock 导出的精确锁定清单（离线 wheels 备灾路径用）
#   wheels/                     全量 wheel（pip download --only-binary，manylinux2014）
#   Python-3.11.14.tar.xz       解释器源码包（目标机无 3.11 时，配合 install_python311.sh）
#   openssl-1.1.1w.tar.gz       同上（系统 OpenSSL < 1.1.1 时需要）
#   mysql-8.0.x-…-minimal.tar.xz 本机 MySQL 裸装介质（--with-mysql；目标机禁 Docker，不带镜像 tar）
#   mc / minio                  备份工具 + 演练 S3 替身静态二进制（--with-mysql 一并下载）
#   qcagent-venv.tar.gz         验证机整 venv 迁移包（主路径，--on-verifier 时打）
#   SHA256SUMS.txt              全清单校验
#
# 用法：
#   开发机（Windows Git Bash / WSL，有网）：
#     cd quality-agent && bash scripts/dev/make_offline_bundle.sh
#   验证机（NFS 3.1，与目标机 glibc 同代，ECS 内网源）追加迁移制品：
#     bash scripts/dev/make_offline_bundle.sh --on-verifier --venv-src=/opt/agent/vision-agent/quality-agent/.venv
#   MySQL 裸装介质/mc 二进制下载（任意有网机器，与 --on-verifier 可同可分）：
#     bash scripts/dev/make_offline_bundle.sh --with-mysql
#
# 可覆盖变量：OUT PY_VERSION OPENSSL_VERSION TARGET_PLATFORMS UVCMD NODE_BUILD MYSQL_VERSION MYSQL_TARBALL_URL MC_URL MINIO_SERVER_URL WEB_SRC
#   WEB_SRC：要交付的 Vue3 前端工程根（默认 ../frontend，即工作区顶层深蓝版）；
#     其 dist 会被植入 quality-agent/frontend/dist 仅作为制品携带（前后端分离：
#     后端不再静态托管，install_server.sh 会把该 dist 投放到 Nginx web 根）。
#     若交付的是 quality-agent 自带前端，则 WEB_SRC=$ROOT/frontend。
#   TARGET_PLATFORMS：pip download 的 --platform 列表（默认 manylinux2014_x86_64
#   + manylinux_2_17 + linux_x86_64 + none-any；glibc 2.28 目标机改传 2_28 系）
# 说明：wheels 备灾路径是兜底；主路径 qcagent-venv.tar.gz 必须在验证机上打
#   （venv 与 glibc/解释器路径强绑定，开发机打的 venv 到目标机必崩）。
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # quality-agent/
OUT="${OUT:-$ROOT/offline-bundle}"
PY_VERSION="${PY_VERSION:-3.11.14}"
OPENSSL_VERSION="${OPENSSL_VERSION:-1.1.1w}"
TARGET_PLATFORMS="${TARGET_PLATFORMS:-manylinux2014_x86_64 manylinux_2_17_x86_64 manylinux_2_28_x86_64 linux_x86_64}"
UVCMD="${UVCMD:-uv}"
ON_VERIFIER=0
WITH_MYSQL=0
VENV_SRC="${VENV_SRC:-/opt/agent/vision-agent/quality-agent/.venv}"
MYSQL_VERSION="${MYSQL_VERSION:-8.0.46}"   # 8.0 线当前 GA；cdn/Downloads/MySQL-8.0/ 只挂当前 GA（旧版走 archives）
MYSQL_TARBALL_NAME="mysql-${MYSQL_VERSION}-linux-glibc2.17-x86_64-minimal.tar.xz"
MYSQL_TARBALL_URL="${MYSQL_TARBALL_URL:-https://dev.mysql.com/get/Downloads/MySQL-8.0/${MYSQL_TARBALL_NAME}}"
MC_URL="${MC_URL:-https://dl.minio.org.cn/client/mc/release/linux-amd64/mc}"   # dl.min.io 国际站已 410 下架，改用国内镜像
MINIO_SERVER_URL="${MINIO_SERVER_URL:-https://dl.minio.org.cn/server/minio/release/linux-amd64/minio}"
NODE_BUILD="${NODE_BUILD:-auto}"   # auto=有 node 就构建 dist；skip=跳过（dist 已就位）
WEB_SRC="$(cd "${WEB_SRC:-$ROOT/../frontend}" 2>/dev/null && pwd || echo "${WEB_SRC:-$ROOT/../frontend}")"  # 要交付的 Vue3 前端工程根

for arg in "$@"; do
  case "$arg" in
    --on-verifier)  ON_VERIFIER=1 ;;
    --with-image)   echo "[bundle][WARN] --with-image 已作废：园区网目标机禁 Docker，不再携带任何容器镜像 tar" >&2 ;;
    --with-mysql)   WITH_MYSQL=1 ;;
    --venv-src=*)   VENV_SRC="${arg#*=}" ;;
    --out=*)        OUT="${arg#*=}" ;;
    *) echo "未知参数：$arg（支持 --on-verifier --with-mysql --venv-src=PATH --out=DIR；--with-image 已作废）" >&2; exit 1 ;;
  esac
done

log(){ printf '\n[bundle] ===== %s =====\n' "$*"; }
warn(){ printf '[bundle][WARN] %s\n' "$*"; }
die(){ printf '[bundle][ERROR] %s\n' "$*" >&2; exit 1; }

mkdir -p "$OUT"
cd "$OUT"

# ---------------------------------------------------------------------------
log "1/6 前端产物（交付的是顶层 Vue3 深蓝版 $WEB_SRC；dist 不入 git，构建后暂存供 nginx 投放）"
# 前后端分离：后端 app/main.py 不再托管静态。本步把要交付的 Vue3 前端 build 产物暂存到
# quality-agent/frontend/dist，仅为了随 source.tar.gz 携带；装机时由 install_server.sh 的
# 5b 步投放到 nginx web 根（WEB_ROOT），nginx 监听对外端口并反代 /api/v1 到后端。
# 顶层前端默认 VITE_QA_API_BASE=/api/v1（同源相对）+ VITE_QA_ENABLED=true，经 nginx 反代可直连平台。
SERVED_DIST="$ROOT/frontend/dist"
if [[ "$WEB_SRC" != "$ROOT/frontend" ]]; then
  [[ -f "$WEB_SRC/package.json" && -f "$WEB_SRC/index.html" ]] || die "WEB_SRC 不是有效前端工程：$WEB_SRC（无 Vue3 版可设 WEB_SRC=$ROOT/frontend）"
fi
if [[ "$NODE_BUILD" != "skip" ]] && command -v npm >/dev/null 2>&1; then
  if [[ "$WEB_SRC" != "$ROOT/frontend" ]]; then
    log "  构建顶层 Vue3 前端：$WEB_SRC"
    ( cd "$WEB_SRC" && npm ci && VITE_QA_ENABLED=true VITE_QA_API_BASE=/api/v1 npm run build )
    log "  植入后端托管路径：$WEB_SRC/dist → $SERVED_DIST"
    rm -rf "$SERVED_DIST"; mkdir -p "$SERVED_DIST"
    cp -a "$WEB_SRC/dist/." "$SERVED_DIST/"
  else
    log "  构建 quality-agent 自带前端：$ROOT/frontend"
    ( cd "$ROOT/frontend" && npm ci && npm run build )
  fi
else
  warn "跳过 npm 构建（无 node 或 NODE_BUILD=skip）—— 确认 $SERVED_DIST 已是要交付的前端产物"
fi
[[ -f "$SERVED_DIST/index.html" ]] || die "$SERVED_DIST/index.html 不存在：先本地构建 Vue3 前端（或去掉 NODE_BUILD=skip）"

# ---------------------------------------------------------------------------
log "2/6 源码包 source.tar.gz（含 dist/uv.lock/deploy/scripts，排除 .env/.venv/node_modules）"
tar --exclude='.venv' --exclude='__pycache__' --exclude='.pytest_cache' \
    --exclude='.git' --exclude='node_modules' --exclude='data' --exclude='logs' \
    --exclude='.env' --exclude='.env.local' \
    --exclude='.pw-browsers' --exclude='.langgraph_api' --exclude='test-results' \
    --exclude='offline-bundle' \
    -czf "$OUT/source.tar.gz" -C "$ROOT" .
[[ -s "$OUT/source.tar.gz" ]] || die "source.tar.gz 生成失败"

# ---------------------------------------------------------------------------
log "3/6 离线锁定清单 + wheels 备灾包（开发机路径；验证机 glibc 同代更稳）"
if command -v "$UVCMD" >/dev/null 2>&1 && [[ -f "$ROOT/uv.lock" ]]; then
  # uv export 需项目根（读 uv.lock）；--quiet 去 stderr 进度/警告噪声（重定向只护 stdout）；
  # 尾部 sed 剔除 uv 附加的 index 行与 section 注释（pip 离线安装不接受这些注入源）
  ( cd "$ROOT" && "$UVCMD" export --frozen --no-dev --no-hashes --quiet -o "$OUT/requirements-offline.txt" \
      && sed -i -E '/^(--index-url|--extra-index-url|--find-links|\[tool-uvs)\b/d; /^# .*section/d; /^-e[[:space:]]/d; /^\.($|\/)/d' "$OUT/requirements-offline.txt" ) \
    || warn "uv export 失败（版本不支持 --no-hashes？手工：uv export --frozen --no-dev | sed 's/#.*//' > requirements-offline.txt）"
else
  warn "uv 不可用或缺 uv.lock —— 跳过 requirements 导出（主路径 venv 迁移不受影响，备灾路径需在验证机补）"
fi
if [[ -f "$OUT/requirements-offline.txt" ]]; then
  mkdir -p "$OUT/wheels"
  PYPK="$(command -v python3 2>/dev/null || command -v python || true)"
  [[ -n "$PYPK" ]] || die "找不到 python：pip download 需要"
  # 只下 wheel（--only-binary）：园区网目标机无 gcc，sdist 必败；
  # 冲突包名/平台无 wheel 会直接报错退出，报错项对照 Runbook 风险表处理。
  "$PYPK" -m pip download -q --only-binary=:all: --no-deps \
    --timeout 60 --retries 1 \
    $(for p in $TARGET_PLATFORMS; do printf -- '--platform %s ' "$p"; done) \
    -d "$OUT/wheels" -r "$OUT/requirements-offline.txt" \
    || warn "wheels 下载有失败项（纯 py 包标记/平台缺口）——备灾包不完整，验证机上以 pip debug --verbose 复核后重下"
  log "  wheels 计数：$(ls "$OUT/wheels" | grep -cE '\.whl$' || true)"
fi

# ---------------------------------------------------------------------------
log "4/6 Python/OpenSSL 源码 tarball（目标机基线缺 3.11 时用，install_python311.sh 已支持离线变量）"
fetch_or_keep(){ # fetch_or_keep <文件名> <url1> [url2...]
  local f="$1"; shift
  if [[ -s "$f" ]]; then log "  已存在：$f（跳过下载）"; return 0; fi
  local url
  for url in "$@"; do
    log "  下载：$url"
    if curl -fL --retry 2 --connect-timeout 10 --max-time 900 -o "$f.part" "$url" \
       && case "$f" in *.tar.xz) xz -t "$f.part" ;; *) gzip -t "$f.part" ;; esac; then
      mv "$f.part" "$f"; return 0
    fi
    rm -f "$f.part"
  done
  return 1
}
fetch_or_keep "Python-$PY_VERSION.tar.xz" \
  "https://registry.npmmirror.com/-/binary/python/$PY_VERSION/Python-$PY_VERSION.tar.xz" \
  "https://www.python.org/ftp/python/$PY_VERSION/Python-$PY_VERSION.tar.xz" \
  || die "Python tarball 下载失败：手工下载后放入 $OUT"
fetch_or_keep "openssl-$OPENSSL_VERSION.tar.gz" \
  "https://www.openssl.org/source/openssl-$OPENSSL_VERSION.tar.gz" \
  || die "OpenSSL tarball 下载失败：手工下载后放入 $OUT"

# --with-mysql：目标机禁 Docker，MySQL 用官方 minimal tarball 裸装，备份/替身用 Go 静态二进制
if [[ "$WITH_MYSQL" == 1 ]]; then
  fetch_or_keep "$MYSQL_TARBALL_NAME" "$MYSQL_TARBALL_URL" \
    || warn "MySQL tarball 下载失败（URL/版本可能有变，不阻断打包）：手工下载正确版本后放入 $OUT，或用 MYSQL_VERSION=/MYSQL_TARBALL_URL= 覆盖重跑"
  warn "别忘了 libaio：演练 VM `ldconfig -p | grep libaio` 缺则用 yumdownloader 拉 RPM 入包（MySQL 裸装唯一系统依赖）"
  fetch_bin(){ # fetch_bin <文件名> <url>；ELF 静态二进制，校验魔数与最小尺寸
    local f="$1" url="$2"
    [[ -s "$f" ]] && { log "  已存在：$f（跳过）"; return 0; }
    log "  下载：$url"
    curl -fL --retry 2 --connect-timeout 10 --max-time 900 -o "$f.part" "$url" \
      && [[ "$(head -c 4 "$f.part" | od -An -tx1 | tr -d ' \n')" == "7f454c46" ]] \
      && [[ "$(stat -c %s "$f.part")" -gt 1000000 ]] \
      && { mv "$f.part" "$f"; chmod 755 "$f"; return 0; }
    rm -f "$f.part"; return 1
  }
  fetch_bin mc "$MC_URL" || warn "mc 二进制下载失败（不阻断打包）：手工取 dl.min.io/client/mc/release/linux-amd64/mc 放入 $OUT"
  fetch_bin minio "$MINIO_SERVER_URL" || warn "minio server 替身二进制下载失败（仅演练需要，园区网现场不依赖）"
  log "  MySQL 裸装介质 + mc/minio 静态二进制已入包（Runbook §Step 5/§Step 10）"
fi

# ---------------------------------------------------------------------------
log "5/6 验证机专属制品（--on-verifier）"
if [[ "$ON_VERIFIER" == 1 ]]; then
  # 主路径：整 venv 迁移（顶层必须名为 .venv，install_server.sh 离线A 依赖此布局；
  # venv 内 shebang/pyvenv.cfg 路径硬编码，要求目标机 APP_DIR 与解释器路径与本机完全一致）
  [[ -x "$VENV_SRC/bin/python" ]] || die "venv 源不存在：$VENV_SRC（--venv-src= 指定）"
  "$VENV_SRC/bin/python" -c 'import fastapi, langgraph' || die "venv 自检失败，不能作迁移源"
  tar -czf "$OUT/qcagent-venv.tar.gz" -C "$(dirname "$VENV_SRC")" "$(basename "$VENV_SRC")"
  log "  qcagent-venv.tar.gz 已生成（目标机解释器路径须同为 /usr/local/bin/python3.11、glibc 同代）"
  # 容器镜像制品已废除：园区网目标机禁 Docker（金相裸装唯一路线；MySQL 走 --with-mysql 静态介质）
else
  log "  跳过（开发机）——qcagent-venv.tar.gz 须在验证机补跑 --on-verifier"
fi

# ---------------------------------------------------------------------------
log "6/6 SHA256SUMS 清单"
find "$OUT" -maxdepth 1 -type f ! -name SHA256SUMS.txt -printf '%f\n' 2>/dev/null \
  | sort > "$OUT/.filelist" || ls "$OUT" | grep -v '^SHA256SUMS.txt$' | sort > "$OUT/.filelist"
( cd "$OUT" && xargs sha256sum < .filelist > SHA256SUMS.txt && rm .filelist )

cat <<EOF

[bundle] 完成。产物（$OUT）：
$(cd "$OUT" && ls -lh | tail -n +2 | awk '{printf "  %-34s %s\n", $9, $5}')

带入园区网后（目标机 root，全程参考 deploy/园区网离线部署Runbook.md）：
  1) sha256sum -c SHA256SUMS.txt                       # 传盘后先验
  2) tar -xzf source.tar.gz -C /root/QualityCheckingAgent
  3) OFFLINE_VENV_TGZ=<bundle>/qcagent-venv.tar.gz bash deploy/install_server.sh /root/QualityCheckingAgent
     # 主路径（venv 迁移要求目标机 APP_DIR=/opt/agent/vision-agent/quality-agent 与解释器路径一致）
     # 备灾：OFFLINE_WHEELS_DIR=<bundle>/wheels bash deploy/install_server.sh …
  4) 金相：裸装（目标机禁 Docker，容器旁路已作废；Runbook §Step 8）
  5) 本机 MySQL：官方 minimal tarball 裸装 + install -m 755 mc /usr/local/bin（Runbook §Step 5/§Step 10）
EOF
