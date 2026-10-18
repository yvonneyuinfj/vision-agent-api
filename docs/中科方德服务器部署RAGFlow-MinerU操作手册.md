# 中科方德服务器部署 RAGFlow + MinerU 操作手册

> 实测沉淀：2026-09-10，在阿里云服务器（中科方德 NFS Server 3.1）上从零安装 Docker / Docker Compose，并按本地（macOS）同款编排部署 RAGFlow v0.27.1（线上要求用最新版）+ MinerU（镜像配方与本地完全一致）。
>
> 本手册所有命令均为实际操作过的命令，可直接复制复现。

---

## 0. 环境信息与结论摘要

### 0.1 服务器实测环境

| 项 | 值 | 备注 |
| --- | --- | --- |
| IP | 182.92.121.228 | 阿里云（北京） |
| 操作系统 | **中科方德 NFS Server 3.1 (RTM4-U1)** | `/etc/os-release`：`ID="NFS"`，`ID_LIKE="NFS"`，nfschina.com |
| 内核 | 3.10.0-514.26.2.nfs.8.x86_64 | CentOS 7.3 同代内核（2016），**决定了 Docker 版本上限** |
| 架构 | x86_64 (amd64) | RAGFlow 官方镜像只有 amd64，原生运行（本地 Mac 是 arm64 跑 Rosetta 模拟，服务器反而更快） |
| CPU / 内存 | 8 核 / 30 GB | 满足 RAGFlow 全栈（官方建议 ≥16 GB） |
| 磁盘 | vda 100G；LVM：n3-root 92G + n3-swap 6G | **初始文件系统只有 32G，需在线扩容（见 §3）** |
| 文件系统 | xfs，`ftype=1` | 满足 overlay2 要求（ftype=0 则不行） |
| SELinux | Disabled | 静态安装 Docker 无 selinux 标签问题 |
| 包管理器 | 仅 `yum`（无 dnf/apt），系统仓库无 docker | 官方 docker-ce 源不识别 `ID_LIKE="NFS"`，**故采用静态二进制安装** |
| 初始 Docker | 未安装 | — |

### 0.2 服务器网络可达性实测（决定各下载渠道）

| 目标 | 结果 | 用途/结论 |
| --- | --- | --- |
| registry-1.docker.io（Docker Hub） | ❌ 不可达 | 必须配国内 registry mirror |
| download.docker.com | ❌ 不可达 | 官方安装脚本/静态包不可用 → 改用阿里云镜像站 |
| mirrors.aliyun.com | ✅ | Docker 静态包、PyPI 镜像 |
| codeload.github.com | ✅（9.3 MB/s） | RAGFlow 源码 tarball 直接在服务器下载 |
| github.com 主页 | ✅ | — |
| objects.githubusercontent.com（GitHub Release 资产） | ❌ | compose 二进制需走加速代理 |
| ghfast.top（GitHub 加速代理） | ✅（约 1.8 MB/s） | 下载 docker compose v5.5.1 |
| docker.m.daocloud.io / docker.1ms.run | ✅（/v2/ 返回 401 属正常） | registry mirror 主力 |
| docker.1panel.live | ✅（200） | registry mirror 备用 |
| deb.debian.org | ✅（约 1s） | mineru 镜像构建时 apt 装运行库，无需换源 |
| modelscope.cn | ✅ | MinerU 首次解析时下载模型权重（约 3.2 GB） |
| mirrors.aliyun.com/pypi（公网） | ✅ 但仅 ~244 kB/s | ⚠ 大轮子（nvidia-*/torch 数 GB）不可接受 |
| **mirrors.cloud.aliyuncs.com/pypi（ECS 内网）** | ✅ **~100 MB/s** | mineru 构建 pip 源最终选用（http + trusted-host） |

### 0.3 最终安装的软件版本（及选型理由）

| 组件 | 版本 | 选型理由 |
| --- | --- | --- |
| Docker Engine | **26.1.4**（静态二进制） | 阿里云 docker-ce centos/7 仓库最高只到 **26.1.4**，即官方在 3.10 内核（CentOS7 系）上支持的最后版本；≥ RAGFlow 要求的 24.0.0。**不要用 27+/29+ 静态包**（官方已不再对 3.10 内核背书） |
| containerd / runc | 随 26.1.4 静态包内置 | — |
| Docker Compose | **v5.5.1**（cli-plugin 二进制） | 本地 Docker Desktop 自带 v5.1.2，同属 v5 系；≥ RAGFlow 要求的 v2.26.1；支持 compose 文件的 `include:` 语法 |
| RAGFlow | **v0.27.1**（infiniflow/ragflow:v0.27.1） | 线上要求最新版；v0.27.1 为 GitHub latest release（2026-08-28）。本地为 v0.27.0 |
| MinerU | **ragflow-mineru:latest**（服务器现场重建） | 该镜像**无公开 registry 版本**，本地即为自建；服务器用同一 Dockerfile + 本地容器 `pip freeze` 出的 125 个包精确版本重建，保证内容一致 |
| entropy-shim | **libentropy-shim.so**（服务器现场编译，§7.4） | 镜像为 Ubuntu 24.04/glibc 2.39，其 `getentropy`/`arc4random_buf` 依赖 `getrandom(2)` 系统调用（需内核 ≥3.17），3.10 内核没有 → **登录/RSA 加解密全挂**。shim 用 `/dev/urandom` 回退，经 `/etc/ld.so.preload` 挂载 + env `LD_PRELOAD` 双通道注入 |

### 0.4 部署产物目录（服务器）

```
/opt/ragflow/
├── docker/                        # RAGFlow v0.27.1 官方 docker 目录（tarball 解压）
│   ├── docker-compose.yml         # 主 compose（include base）
│   ├── docker-compose-base.yml    # mysql/es/valkey/silo/redis 等基础服务
│   ├── docker-compose-mineru.yml  # MinerU overlay（自定义，从本地上传，上游不含）
│   ├── docker-compose-entropy-fix.yml # 熵源修复 overlay（内核 3.10 必装，见 §7.4）
│   ├── .env                       # 已启用 MINERU 两行 + UI 端口 8080/8443 + COMPOSE_FILE（三合一）
│   ├── service_conf.yaml.template # 挂载进 ragflow 容器
│   ├── entrypoint.sh              # 挂载进 ragflow 容器
│   └── mineru-models{,-paddlex}/  # MinerU 模型缓存卷（首次解析后生成，约 3.2G）
├── mineru-build/                  # MinerU 镜像构建上下文
│   ├── Dockerfile
│   └── requirements.txt           # 本地 mineru 容器 pip freeze（125 包）
├── entropy-shim/                  # 熵源修复（§7.4）：shim.c / libentropy-shim.so / ld.so.preload / test_arc4.c
├── pull-build.sh                  # 拉取+构建流水线脚本
├── pull-build.log                 # 流水线日志
└── ragflow-v0.27.1.tar.gz         # 源码包（留档）
/opt/docker-install/               # docker 静态包与解压产物（留档）
```

---

## 1. SSH 登录与免密配置

服务器初始仅支持密码登录。为便于反复操作，先装公钥（macOS 无 sshpass，用系统自带 expect 驱动一次密码输入）：

```bash
# 本地（macOS）执行；<SSH密码> 见内部交接渠道，不落盘
expect -c '
set timeout 45
spawn ssh-copy-id -o StrictHostKeyChecking=accept-new \
  -o PreferredAuthentications=password -o PubkeyAuthentication=no \
  -i ~/.ssh/id_ed25519.pub root@182.92.121.228
expect {
  -re "(?i)password:" { send "<SSH密码>\r"; exp_continue }
  eof {}
}
'
```

之后配置快捷命令（本手册后续用 `srv_ssh` 指代）：

```bash
# 免密验证
ssh -o BatchMode=yes root@182.92.121.228 'echo LOGIN_OK; hostname'
```

> 踩坑：DSH/沙箱环境下 expect 需要 pty，若报 `The system has no more ptys` 属沙箱限制，需放开权限执行一次。

---

## 2. 系统探测（部署前必查清单）

```bash
ssh root@182.92.121.228 'bash -s' <<'EOF'
cat /etc/os-release | head -6      # 确认 ID="NFS"（中科方德）
uname -m                            # x86_64 —— 若是 aarch64/loongarch 则 RAGFlow 官方镜像不可用！
uname -r                            # 3.10.0-514 → 决定 Docker 版本上限 26.1.x
nproc; free -h | head -2            # 8C/30G
df -h /; lsblk                      # ⚠ 发现 LV 92G 但文件系统只有 32G → 见 §3
df -T /; xfs_info / | grep ftype    # xfs 且 ftype=1 → overlay2 可用
getenforce                          # Disabled → 无需处理 SELinux
command -v docker yum dnf           # docker 未装；只有 yum
EOF
```

网络探测（决定下载渠道，见 §0.2 结果表）：

```bash
for u in https://download.docker.com https://mirrors.aliyun.com \
         https://registry-1.docker.io/v2/ https://codeload.github.com \
         https://objects.githubusercontent.com https://deb.debian.org; do
  printf "%s => " "$u"; curl -sI --max-time 10 -o /dev/null -w "%{http_code}\n" "$u" || echo FAIL
done
# registry mirror 候选（401/200/302 都算通）
for m in docker.m.daocloud.io docker.1ms.run docker.1panel.live; do
  printf "%s => " $m; curl -s --max-time 8 -o /dev/null -w "%{http_code}\n" https://$m/v2/
done
```

---

## 3. 根文件系统在线扩容（32G → 92G）⚠ 必做

`lsblk` 显示 LV `n3-root` 有 92G，但 `df` 只有 32G——厂商镜像扩容了 LV 却没扩文件系统。RAGFlow+MinerU 镜像合计约 25G+，不扩容必然爆盘。

xfs 支持挂载状态下在线扩容，无风险：

```bash
xfs_growfs /
# data blocks changed from 8387584 to 24116224
df -h /
# /dev/mapper/n3-root   92G  4.1G   88G    5% /
```

---

## 4. 内核参数（Elasticsearch / Docker 网络前置条件）

```bash
# ES 要求 vm.max_map_count >= 262144（默认 65530 会导致 es01 起不来）
sysctl -w vm.max_map_count=262144
echo "vm.max_map_count=262144" >> /etc/sysctl.conf
echo "net.ipv4.ip_forward=1"  >> /etc/sysctl.conf
```

（overlay / br_netfilter 模块与 bridge sysctl 在 §5 安装脚本里一并处理。）

---

## 5. 安装 Docker 26.1.4（静态二进制 + systemd）

**为什么不用 yum/docker-ce 源**：中科方德 `ID_LIKE="NFS"`，docker-ce 的 centos 源 `$releasever` 对不上，且系统仓库无 container-selinux 等依赖；静态二进制零依赖、版本精确可控，是国产 OS 上最稳的方式。

**为什么是 26.1.4**：内核 3.10 的官方支持上限（阿里云 centos/7 docker-ce 仓库最后一版即 26.1.4）；RAGFlow 要求 ≥24.0.0。

### 5.1 下载与安装二进制

```bash
mkdir -p /opt/docker-install && cd /opt/docker-install
curl -fSL --retry 3 -o docker-26.1.4.tgz \
  https://mirrors.aliyun.com/docker-ce/linux/static/stable/x86_64/docker-26.1.4.tgz
tar xzf docker-26.1.4.tgz
cp -f docker/* /usr/local/bin/     # docker dockerd containerd runc ctr docker-proxy 等
docker --version && containerd --version && runc --version | head -1
groupadd docker || true            # docker.socket 属组需要
```

### 5.2 systemd 单元（3 个文件）

`/etc/systemd/system/containerd.service`：

```ini
[Unit]
Description=containerd container runtime
Documentation=https://containerd.io
After=network.target local-fs.target

[Service]
ExecStartPre=-/sbin/modprobe overlay
ExecStart=/usr/local/bin/containerd
Type=notify
Delegate=yes
KillMode=process
Restart=always
RestartSec=5
LimitNPROC=infinity
LimitCORE=infinity
LimitNOFILE=infinity
TasksMax=infinity
OOMScoreAdjust=-999

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/docker.service`：

```ini
[Unit]
Description=Docker Application Container Engine
Documentation=https://docs.docker.com
After=network-online.target docker.socket firewalld.service containerd.service time-set.target
Wants=network-online.target
Requires=containerd.service

[Service]
Type=notify
ExecStart=/usr/local/bin/dockerd -H fd:// --containerd=/run/containerd/containerd.sock
ExecReload=/bin/kill -s HUP $MAINPID
TimeoutStartSec=0
RestartSec=2
Restart=always
StartLimitBurst=3
StartLimitInterval=60s
LimitNPROC=infinity
LimitCORE=infinity
LimitNOFILE=infinity
TasksMax=infinity
Delegate=yes
KillMode=process
OOMScoreAdjust=-500

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/docker.socket`：

```ini
[Unit]
Description=Docker Socket for the API

[Socket]
ListenStream=/run/docker.sock
SocketMode=0660
SocketUser=root
SocketGroup=docker

[Install]
WantedBy=sockets.target
```

### 5.3 daemon.json（镜像加速 + 日志轮转 + overlay2）

`/etc/docker/daemon.json`：

```json
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://docker.1ms.run",
    "https://docker.1panel.live"
  ],
  "log-driver": "json-file",
  "log-opts": { "max-size": "100m", "max-file": "3" },
  "storage-driver": "overlay2"
}
```

> Docker Hub 在境内不可直连，`registry-mirrors` 是拉取一切 Hub 镜像（含 `infiniflow/ragflow`）的生命线；三个 mirror 互为备份（docker 会自动按序重试）。日志轮转防单容器日志爆盘。

### 5.4 内核模块与 bridge 参数

```bash
cat > /etc/modules-load.d/docker.conf <<'MOD'
overlay
br_netfilter
MOD
modprobe overlay && modprobe br_netfilter

cat > /etc/sysctl.d/99-docker.conf <<'SYS'
net.bridge.bridge-nf-call-iptables=1
net.bridge.bridge-nf-call-ip6tables=1
net.ipv4.ip_forward=1
SYS
sysctl --system > /dev/null
```

### 5.5 启动与验证

```bash
systemctl daemon-reload
systemctl enable --now containerd
systemctl enable --now docker.socket docker.service
systemctl is-active containerd docker          # active active
docker version --format "client={{.Client.Version}} server={{.Server.Version}}"   # 26.1.4 / 26.1.4
docker info | grep -E "Server Version|Storage Driver|Cgroup|Kernel|Registry Mirrors" -A1
# 端到端验证（走 mirror 拉取并运行）
docker run --rm hello-world
```

实测输出要点：`Storage Driver: overlay2`，`Cgroup Driver: cgroupfs / Cgroup Version: 1`（3.10 内核只有 cgroup v1，正常），`Operating System: NFS Server 3.1 (RTM4-U1)`。

---

## 6. 安装 Docker Compose v5.5.1

服务器直连 GitHub Release 资产域名（objects.githubusercontent.com）被墙，走 `ghfast.top` 加速代理：

```bash
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fSL --retry 2 -o /usr/local/lib/docker/cli-plugins/docker-compose \
  "https://ghfast.top/https://github.com/docker/compose/releases/download/v5.5.1/docker-compose-linux-x86_64"
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
ln -sf /usr/local/lib/docker/cli-plugins/docker-compose /usr/local/bin/docker-compose  # 兼容旧命令
docker compose version    # Docker Compose version v5.5.1
```

---

## 7. 部署 RAGFlow v0.27.1（官方 compose，与本地同构）

### 7.1 下载官方 v0.27.1 docker 目录

服务器可直连 codeload.github.com（实测 9.3 MB/s），无需从本地传包：

```bash
mkdir -p /opt/ragflow && cd /opt/ragflow
curl -fSL --retry 3 -o ragflow-v0.27.1.tar.gz \
  https://codeload.github.com/infiniflow/ragflow/tar.gz/refs/tags/v0.27.1
tar xzf ragflow-v0.27.1.tar.gz
mv ragflow-0.27.1/docker /opt/ragflow/docker
rm -rf ragflow-0.27.1
```

> 只需要 `docker/` 目录：compose、`.env`、`service_conf.yaml.template`、`entrypoint.sh`、`init.sql`、nginx 配置全在里面。deepdoc 等服务在 compose 里挂了 profile，不启用就不会触发对仓库其余部分的构建依赖。

### 7.2 .env 关键项（v0.27.1 默认值 + MinerU 两行）

v0.27.1 出厂 `.env` 默认即为最新镜像与本地同款基础栈，**只需启用 MinerU 两行**：

```bash
cd /opt/ragflow/docker
sed -i 's|^# MINERU_BACKEND=pipeline.*|MINERU_BACKEND=pipeline|' .env
sed -i '/^MINERU_BACKEND=pipeline/i MINERU_APISERVER=http://mineru:8001' .env
grep -n '^MINERU' .env
# 386:MINERU_APISERVER=http://mineru:8001
# 387:MINERU_BACKEND=pipeline
```

与本地一致的关键默认值（无需改动）：

| 变量 | 值 | 说明 |
| --- | --- | --- |
| `RAGFLOW_IMAGE` | `infiniflow/ragflow:v0.27.1` | 出厂即最新 release（本地是 v0.27.0，线上按要求用最新） |
| `DOC_ENGINE` | `elasticsearch` | 与本地一致（另有 infinity 可选） |
| `STACK_VERSION` | `8.11.3` | ES 版本，与本地一致 |
| `DEVICE` | `cpu` | 与本地一致 |
| `METADATA_DB_PROFILE` | `mysql` | 与本地一致（mysql:8.0.40） |
| `COMPOSE_PROFILES` | `${DOC_ENGINE},${DEVICE},metadata-${METADATA_DB_PROFILE}` | 展开 = elasticsearch,cpu,metadata-mysql |
| `MEM_LIMIT` | 8073741824（7.5G） | ES 内存上限，30G 内存下无压力 |
| 端口 | UI **8080/8443**（`SVR_WEB_HTTP_PORT=8080`、`SVR_WEB_HTTPS_PORT=8443`，**腾出 80/443 给质检智能体**；本地仍为 80/443），API 9380、admin 9381/9383、MCP 9382、go 9384、ES 1200、MySQL 3306、MinIO(silo) 9000/9001、Redis(valkey) 6379 | 仅 UI 端口两项为有意差异（§10.3），其余与本地一致 |
| 密码 | `infini_rag_flow`（MySQL/Redis/ES/MinIO 同） | v0.27.1 默认，与本地一致 |

> ⚠ `MINERU_APISERVER` 在代码里**没有默认值**（`rag/llm/ocr_model.py` 读到空串直接报 "MINERU_APISERVER not configured"），必须显式写 `http://mineru:8001`（容器网络内用服务名，不能写 localhost）。`MINERU_BACKEND` 代码默认 pipeline，写出来更明确。这两个变量会让 RAGFlow 启动时自动把 MinerU 注册为 OCR model provider。

### 7.3 MinerU compose overlay（自定义文件，上游不含）

`docker-compose-mineru.yml` 是本地自写的 overlay（上游 v0.27.1 没有），从本地 scp 上传：

```bash
scp /Users/ych/Code/personal/python/ragflow/docker/docker-compose-mineru.yml \
    root@182.92.121.228:/opt/ragflow/docker/
```

内容（与本地逐字一致）：

```yaml
services:
  mineru:
    image: ${MINERU_IMAGE:-ragflow-mineru:latest}
    container_name: mineru
    command: ["mineru-api", "--host", "0.0.0.0", "--port", "8001"]
    networks:
      - ragflow
    environment:
      - MINERU_MODEL_SOURCE=${MINERU_MODEL_SOURCE:-modelscope}
      - HF_ENDPOINT=${HF_ENDPOINT:-https://hf-mirror.com}
    ports:
      - ${MINERU_PORT:-8001}:8001
    volumes:
      - ./mineru-models:/root/.cache
      - ./mineru-models-paddlex:/root/.paddlex
    restart: unless-stopped
```

---

### 7.4 内核 3.10 熵源修复（entropy shim）⚠ 必做，否则无法登录

**症状**：UI/API/版本接口全部 200 正常，但登录始终 `{"code":500,"message":"Fail to crypt password"}`；容器日志出现
`Fatal glibc error: cannot get entropy for arc4random`。

**根因**（全部本机实测复现）：

| 环节 | 实测 |
| --- | --- |
| 镜像系统 | Ubuntu 24.04.4 + glibc 2.39（**v0.27.0 与 v0.27.1 同底**，降版本无用） |
| `getrandom(2)` | 内核 3.17 才引入；本机 3.10 上 `syscall(318)` 返回 **ENOSYS(errno 38)** |
| glibc 2.39 `getentropy()` | ENOSYS 时**不回退** `/dev/urandom`，直接返回 -1（实测 errno=38） |
| glibc 2.39 `arc4random_buf()` | 取不到熵即 `__libc_fatal` **abort 进程**（实测 Fatal） |
| 对照 | Debian 12/glibc 2.36 的 arc4random 有 urandom 回退（同内核 C 程序原生通过）→ 越新的 glibc 反而砍了回退 |
| 影响面 | RAGFlow 登录密码走 RSA 加解密（PyCryptodome 3.20.0，`api/utils/crypt.py`）→ RNG 挂 → **登录/改密/注册全挂** |
| 不受影响 | Python `os.urandom`（CPython 自带回退）、OpenSSL（自带回退）→ 服务主体正常，**易误判为"部署成功"** |

**修复**：15KB shim 库，把两个符号实现为 `/dev/urandom` 回退（urandom 即 CSPRNG，密码学强度不变）。

1）`/opt/ragflow/entropy-shim/shim.c`（完整源码）：

```c
/* libentropy-shim: getentropy()/arc4random_buf() via /dev/urandom.
   Fixes "Fatal glibc error: cannot get entropy for arc4random" when running
   Ubuntu 24.04 (glibc 2.39) containers on pre-3.17 kernels (no getrandom(2)). */
#define _GNU_SOURCE
#include <stddef.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>

static int urandom_read(void *buf, size_t len)
{
    int fd = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    unsigned char *p = (unsigned char *)buf;
    size_t got = 0;
    while (got < len) {
        ssize_t r = read(fd, p + got, len - got);
        if (r < 0) { if (errno == EINTR) continue; close(fd); return -1; }
        if (r == 0) break;
        got += (size_t)r;
    }
    close(fd);
    return got == len ? 0 : -1;
}

int getentropy(void *buffer, size_t length)
{
    if (length > 256) { errno = EIO; return -1; }
    if (urandom_read(buffer, length) == 0) return 0;
    errno = EIO;
    return -1;
}

void arc4random_buf(void *buf, size_t nbytes)
{
    size_t done = 0;
    while (done < nbytes) {
        size_t chunk = nbytes - done;
        if (chunk > 256) chunk = 256;
        if (urandom_read((char *)buf + done, chunk) != 0) return;
        done += chunk;
    }
}
```

编译（宿主机无 gcc，用一次性容器编；apt 走 ECS 内网源，同 §8 坑位）：

```bash
docker run --rm -v /opt/ragflow/entropy-shim:/out python:3.12-slim sh -c \
  "sed -i 's|deb.debian.org|mirrors.cloud.aliyuncs.com|g' /etc/apt/sources.list.d/debian.sources \
   && apt-get update -qq && apt-get install -y -qq gcc >/dev/null \
   && gcc -shared -fPIC -O2 -Wall -o /out/libentropy-shim.so /out/shim.c && echo SHIM_BUILT"
echo /usr/local/lib/libentropy-shim.so > /opt/ragflow/entropy-shim/ld.so.preload
```

2）overlay `/opt/ragflow/docker/docker-compose-entropy-fix.yml`：

```yaml
services:
  ragflow-cpu:
    volumes:
      - /opt/ragflow/entropy-shim/libentropy-shim.so:/usr/local/lib/libentropy-shim.so:ro
      - /opt/ragflow/entropy-shim/ld.so.preload:/etc/ld.so.preload:ro
    environment:
      - LD_PRELOAD=/usr/local/lib/libentropy-shim.so
```

**为什么必须挂 `/etc/ld.so.preload`（本节核心坑）**：entrypoint 启动 task executor 用的是

```bash
LD_PRELOAD="$JEMALLOC_PATH" "$PY" rag/svr/task_executor.py -i ... &   # task_exe()
```

env `LD_PRELOAD` 被 jemalloc **整体顶掉**——实测 executor 首次尝试即 abort（日志一次 Fatal，`task_exe()` 的 `while true; wait` 重启循环兜底复活，但存在解析任务中途再挂的风险）。`/etc/ld.so.preload` 由动态链接器直接读取，与 env `LD_PRELOAD` **叠加**、无法被顶掉 → executor 同时获得 jemalloc 与 shim（实测 `/proc/<pid>/maps` 两库并存）。镜像原生**无** `/etc/ld.so.preload`（挂载前实测 ABSENT），覆盖挂载安全。

3）`.env` 追加 `COMPOSE_FILE`（三个 compose 文件合一，此后裸命令即可）：

```bash
echo 'COMPOSE_FILE=docker-compose.yml:docker-compose-mineru.yml:docker-compose-entropy-fix.yml' >> /opt/ragflow/docker/.env
cd /opt/ragflow/docker && docker compose up -d ragflow-cpu
```

**验证**（2026-09-10 实测全部通过）：

```bash
# ① venv RNG 不再挂
docker exec docker-ragflow-cpu-1 /ragflow/.venv/bin/python -c \
  "from Cryptodome.Random import get_random_bytes; print('RNG_OK', get_random_bytes(8).hex())"
# ② 重启后 Fatal 计数 = 0
docker logs docker-ragflow-cpu-1 2>&1 | grep -c "Fatal glibc"
# ③ executor 进程 maps 两库并存（jemalloc + shim）
docker exec docker-ragflow-cpu-1 sh -c \
  'grep -oE "(libjemalloc[^ ]*|libentropy-shim\.so)" /proc/$(pgrep -f task_executor | head -1)/maps | sort -u'
# ④ RSA 加密登录（浏览器等价流程）→ code 0 + Set-Cookie: session=...
# ⑤ 带 Cookie GET /api/v1/datasets → {"code":0,"data":[]}
```

> ⚠ 两个易误诊点（本地行为相同，非服务器故障）：
> ① **明文密码**直发登录接口返回 `Fail to crypt password`(500) 属**正常语义**——v0.27 前端先用 `conf/public.pem` 做 RSA 加密再提交（`api/utils/crypt.py`：`decrypt(crypt(x)) == base64(x)`）。命令行验证请复刻上面第④步的加密脚本。
> ② 登录成功响应是 `code:0` + **session cookie**（Flask session，body data 为用户信息），v0.27.1 web 登录**不发 access_token**；程序化访问用 UI「API」页的 API Key（`Authorization: Bearer ragflow-...`）。

---

## 8. 重建 MinerU 镜像（与本地内容一致）

`ragflow-mineru:latest` 无公开镜像源，**必须在服务器现场重建**。为保证"镜像内容一致"：

1. **Dockerfile 层序与本地 `docker history` 逆向出的配方逐层一致**（python:3.12-slim → WORKDIR /srv → COPY requirements → pip install → EXPOSE → CMD → apt 运行库）；
2. **requirements.txt 不用裸的 `mineru[core]==3.4.5`，而用本地 mineru 容器 `pip freeze` 的 125 个包精确版本**（本地：`docker exec mineru pip freeze > requirements.txt`），杜绝传递依赖漂移；
3. 唯一差异：pip 走 **阿里云 ECS 内网 PyPI 镜像**（同源全量镜像，仅下载渠道不同）；apt 直连 deb.debian.org（实测可达，不换源）。

> ⚠ 实测坑：ECS 走**公网** mirrors.aliyun.com/pypi 只有 ~244 kB/s（nvidia-*/torch 数 GB 轮子要下几小时）；换**内网** mirrors.cloud.aliyuncs.com 实测 ~100 MB/s。内网源是 http 协议，pip 需带 `--trusted-host`。

本地导出 freeze（macOS）：

```bash
docker exec mineru pip freeze > /tmp/mineru-requirements-freeze.txt   # 125 行
grep -E '^mineru|^torch' /tmp/mineru-requirements-freeze.txt
# mineru==3.4.5 / mineru_vl_utils==1.2.1 / torch==2.13.0 / torchvision==0.28.0
scp /tmp/mineru-requirements-freeze.txt root@182.92.121.228:/opt/ragflow/mineru-build/requirements.txt
```

服务器 `/opt/ragflow/mineru-build/Dockerfile`：

```dockerfile
# ragflow-mineru:latest —— 与本地构建配方一致
# 本地(arm64)构建自: python:3.12-slim + requirements.txt(mineru[core]==3.4.5 全量 freeze) + X11/GL 运行库
# 服务器(amd64)重建: 同一 Dockerfile 层序, requirements.txt 为本地容器 pip freeze(125 个包精确版本)
# 唯一差异: pip 走阿里云 ECS 内网 PyPI 镜像(内容同源, 仅下载源不同; 公网 aliyun 源实测仅 ~244kB/s, 内网 ~100MB/s)
FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -i http://mirrors.cloud.aliyuncs.com/pypi/simple/ --trusted-host mirrors.cloud.aliyuncs.com -r requirements.txt
EXPOSE 8001
CMD ["mineru-api", "--host", "0.0.0.0", "--port", "8001"]
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends libxcb1 libgl1 libglib2.0-0 >/dev/null 2>&1 && rm -rf /var/lib/apt/lists/* && echo APT_OK
```

> 已知合理差异（记录备查）：
> - 架构：本地 arm64 / 服务器 amd64——所有镜像 ID 必然不同，但 repo:tag 与软件包版本逐一相同；
> - freeze 清单里含 15 个 `nvidia-*` CUDA 包（torch 2.13.0 的依赖，本地 arm64 容器同样安装了它们，这也是本地镜像 9.27G 的主要构成）；服务器 amd64 构建会装同一批包（x86_64 wheel），无 GPU 也照常以 CPU 模式运行，两侧镜像体积量级一致；
> - `python:3.12-slim` 为浮动 tag：本地构建时是 3.12.14，服务器构建时以 `docker run --rm ragflow-mineru:latest python3 --version` 实测为准（见 §10 验证表）。

---

## 9. 拉取镜像 + 构建 + 启动

### 9.1 后台流水线（拉取与构建总耗时较长，用 nohup 挂后台）

`/opt/ragflow/pull-build.sh`：

```bash
#!/bin/bash
cd /opt/ragflow/docker
echo "=== [$(date +%F_%T)] compose pull start ==="
docker compose -f docker-compose.yml pull          # 按 .env profiles 拉 ragflow/es/mysql/silo/valkey
echo "=== [$(date +%F_%T)] compose pull rc=$? / mineru build start ==="
docker build -t ragflow-mineru:latest /opt/ragflow/mineru-build
echo "=== [$(date +%F_%T)] mineru build rc=$? ==="
docker images
echo PIPELINE_ALL_DONE
```

```bash
nohup bash /opt/ragflow/pull-build.sh > /opt/ragflow/pull-build.log 2>&1 &
tail -f /opt/ragflow/pull-build.log        # 观察进度（Ctrl-C 退出不影响后台）
```

> 说明：`docker compose pull` 自动读取 `.env` 的 `COMPOSE_PROFILES=elasticsearch,cpu,metadata-mysql`，只会拉取启用 profile 的服务镜像；mineru 服务因镜像需本地构建，不在 pull 阶段处理（overlay 文件也不加入 pull 命令）。

### 9.2 启动全栈

`.env` 已写入 `COMPOSE_FILE=docker-compose.yml:docker-compose-mineru.yml:docker-compose-entropy-fix.yml`（§7.4），裸命令自动带上三个文件：

```bash
cd /opt/ragflow/docker
docker compose up -d
docker compose ps
```

> ⚠ 若显式用 `-f`，**必须三个文件一起带**（`-f` 会覆盖 `COMPOSE_FILE`；漏掉 entropy-fix 会重建出不带 shim 的容器 → 登录再次损坏）。本地（Mac）仍是两个 `-f` 的形态（新内核无需 shim）。

预期容器（6 个，与本地一致）：

| 容器 | 镜像 | 端口（宿主机） |
| --- | --- | --- |
| docker-ragflow-cpu-1 | infiniflow/ragflow:v0.27.1 | **8080, 8443**, 9380-9384（UI 端口腾给质检智能体，§10.3） |
| docker-es01-1 | elasticsearch:8.11.3 | 1200→9200 |
| docker-mysql-1 | mysql:8.0.40 | 3306 |
| docker-minio-1 | pgsty/silo:RELEASE.2026-08-06T00-00-00Z | 9000, 9001 |
| docker-redis-1 | valkey/valkey:8 | 6379 |
| mineru | ragflow-mineru:latest（现场构建） | 8001 |

### 9.3 启动实测记录（2026-09-10）

| 阶段 | 实测耗时 | 备注 |
| --- | --- | --- |
| compose pull（5 镜像约 10 GB，走 daocloud mirror） | ~15 分钟 | ragflow v0.27.1（7.69GB）最大 |
| mineru 镜像构建（pip 125 包 + apt 3 库） | ~17 分钟 | 全程内网源；公网源方案已放弃（见 §8 坑位说明） |
| up -d → mysql/es/minio/redis 全部 healthy | ~3 分钟 | ragflow 首启自动初始化 DB（rag_flow 库）与默认管理员 |
| UI/API/MinerU 端点全部可用 | ~5 分钟 | 见 §10 |
| MinerU 模型预热（首次解析触发下载 2.4GB+） | ~7 分钟 | ModelScope 国内源实测 200MB/s+，见 §12.2 |

> 首启（shim 挂载前）日志曾有一次 `Fatal glibc error: cannot get entropy for arc4random`：task executor 首次尝试 abort（jemalloc 顶掉 env LD_PRELOAD，§7.4），被重启循环兜底。挂载 `/etc/ld.so.preload` 重建后实测 **Fatal 计数 0**。再看到这行：先查 compose 是否漏了 entropy-fix overlay。

---

## 10. 验证清单

### 10.1 容器状态（`docker compose ps`；COMPOSE_FILE 已含三文件，§7.4）

| 容器 | 镜像 | 状态（实测） |
| --- | --- | --- |
| docker-ragflow-cpu-1 | infiniflow/ragflow:v0.27.1 | Up |
| docker-es01-1 | elasticsearch:8.11.3 | Up (healthy) |
| docker-mysql-1 | mysql:8.0.40 | Up (healthy) |
| docker-minio-1 | pgsty/silo:RELEASE.2026-08-06T00-00-00Z | Up (healthy) |
| docker-redis-1 | valkey/valkey:8 | Up (healthy) |
| mineru | ragflow-mineru:latest | Up（Uvicorn running on 0.0.0.0:8001） |

### 10.2 端点验证（服务器本机实测）

| 检查项 | 命令 | 实测结果 |
| --- | --- | --- |
| UI 首页 | `curl -sI http://localhost:8080/` | `HTTP/1.1 200 OK`，nginx/1.31.3，页面 `<title>RAGFlow</title>`（UI 已迁至 8080，§10.3） |
| RAGFlow 版本 API | `curl http://localhost:9380/api/v1/system/version` | `{"code":0,"data":"v0.27.1","message":"success"}` |
| 外部 API 鉴权语义 | `curl http://localhost:9380/api/v1/datasets` | `{"code":401,...Unauthorized}`（未带 key，语义正确） |
| **Web 登录**（RSA 加密，§7.4 验证节脚本） | 容器内 `/ragflow/.venv/bin/python` 复刻前端加密 → `POST http://localhost:9380/api/v1/auth/login` | `{"code":0,...}` + `Set-Cookie: session=...`；**明文密码直发返回 `Fail to crypt password`(500) 属正常语义**（本地相同，前端必须 RSA 加密） |
| MinerU API 文档 | `curl -o /dev/null -w %{http_code} http://localhost:8001/docs` | `200` |
| MinerU OpenAPI | `curl http://localhost:8001/openapi.json` | 含 `POST /file_parse` |
| MinerU 真实解析 | `curl -F files=@test.pdf http://localhost:8001/file_parse` | `{"status":"completed","backend":"hybrid-engine",...}`（41s，模型已预热） |
| Elasticsearch | `curl -u elastic:infini_rag_flow http://localhost:1200` | cluster `docker-cluster`，version 8.11.3 |
| 默认管理员落库 | `mysql -uroot -pinfini_rag_flow rag_flow -e "select email,password from user"` | `admin@ragflow.io` / `YWRtaW4=`（= base64("admin")，与源码 `init_default_admin()` 一致） |
| 会话数据面（等价登录后的 UI 状态） | 带登录 Cookie `GET /api/v1/datasets?page=1&page_size=5` | `{"code":0,"data":[]}`（空库正常；web 列表接口即此 RESTful 路径 + session 鉴权，`api.ts` 的 `kbList`） |
| 熵源修复生效（§7.4） | executor/server 进程 `/proc/<pid>/maps`；`docker logs ... \| grep -c "Fatal glibc"` | `libentropy-shim.so` 与 `libjemalloc.so.2` 并存；重启后 Fatal 计数 **0** |

> 登录端点勘误：v0.27 前端实际使用 **`POST /api/v1/auth/login`**（`web/src/utils/api.ts`），旧路径 `/v1/user/login` 已不存在（本地与服务器对其返回**相同**的 404，行为一致，非部署问题）。登录成功 v0.27.1 发的是 **session cookie**（不发 access_token）；程序化访问用 UI「API」页 API Key（`Bearer ragflow-...`）。

### 10.3 外网可达性与阿里云安全组端口规划

2026-09-10 调整：RAGFlow UI 已从 80/443 迁至 **8080/8443**（`.env` 的 `SVR_WEB_HTTP_PORT/SVR_WEB_HTTPS_PORT`），**80/443 腾给质检智能体平台**（本项目，计划跑 80；实测两端口现已空闲）。

**阿里云安全组（入方向）配置清单**：

| 端口 | 用途 | 安全组 | 说明 |
| --- | --- | --- | --- |
| 22 | SSH | ✅ 放行（**强烈建议限源 IP**） | 在用 |
| 80 | 质检智能体平台（UI+API 同端口） | ✅ 放行 | 服务器已腾出；平台部署时 `.env` 写 `API_PORT=80`（项目默认 8000） |
| 8080 | RAGFlow Web UI | ✅ 放行 | `http://182.92.121.228:8080/`（放行后即可达） |
| 9380 | RAGFlow HTTP API | ⚠ 按需 | **质检智能体部署在同机 → 走 `http://localhost:9380`，不放行**；仅外部机器（如 Mac 开发调试）直连时放行并限源 IP |
| 443 / 8443 | https | 暂不放行 | 8443 = RAGFlow 自签 https（实际不用）；443 留给质检智能体将来上 TLS |
| 8001 | MinerU API | ❌ **不放行** | RAGFlow 走容器内网 `http://mineru:8001` 调用 |
| 3306/6379/1200/9000/9001 | MySQL/Redis/ES/MinIO | ❌ **不放行** | 仅限同机 localhost 访问；远程调试用 SSH 隧道 `ssh -L 9001:localhost:9001 root@182.92.121.228` |
| 9381-9384 | admin/MCP/go | ❌ 不放行 | MCP 默认未启用（实测 9382-9384 本地与服务器两侧都 refused、9381 两侧都 404，行为一致） |
| 5432 | 质检智能体 checkpointer PostgreSQL（规划中） | ❌ 不放行 | 同机 localhost 访问 |

> 出方向：阿里云安全组默认全放行出站，平台调 LLM 网关（443 出站）无需配置。
> 质检智能体同机部署的 `.env` 写法见 §12.3（RAGFlow/MinIO/MySQL 全走 localhost，零额外安全组开放）。

### 10.4 默认账号与安全基线

- Web UI 登录：**http://182.92.121.228:8080/** → **admin@ragflow.io / admin**（v0.27.1 首启自动创建；登录后立即改密码）。
- 勘误：`admin/server/config.py` 里的 `infini_rag_flow` 是 9381 管理端口密码，不是 Web 密码——旧笔记"admin/infini_rag_flow"系混淆。
- ⚠ 交付前务必：UI 内修改 admin 密码；在 UI「API」页生成 API Key 更新平台 `.env` 的 `RAGFLOW_API_KEY`。
- MySQL/Redis/ES/MinIO 密码均为出厂默认 `infini_rag_flow` 且端口映射在宿主机（3306/6379/1200/9000），安全组**不要**放行这些端口。

---

## 11. 本地 ↔ 服务器镜像一致性对照

镜像 ID 因架构（本地 Apple Silicon=arm64，服务器=amd64）必然不同，一致性以 **repo:tag + 软件版本** 为准：

| 镜像 | 本地（arm64） | 服务器（amd64） | 一致性说明 |
| --- | --- | --- | --- |
| infiniflow/ragflow | v0.27.0（026908a5d894，11.8GB） | **v0.27.1**（8d997d7ba19c，7.69GB） | 按需求线上用最新版；v0.27.1 = GitHub latest release（2026-08-28） |
| mysql | 8.0.40（d58ac93387f6） | 8.0.40（6c55ddbef969） | 同 tag ✅ |
| elasticsearch | 8.11.3（58a3a280935d） | 8.11.3（ac1eef415132） | 同 tag ✅（v0.27.1 compose 的 STACK_VERSION 与本地相同） |
| valkey/valkey | 8（495e4fecdc98） | 8（8f7b0d3d7a7c） | 同 tag ✅ |
| pgsty/silo | RELEASE.2026-08-06T00-00-00Z（29a498b24669） | RELEASE.2026-08-06T00-00-00Z（7d95c9dd122b） | 同 tag ✅（v0.27.1 base compose 与本地同版本） |
| ragflow-mineru | latest（40328eb97ad3，9.27GB） | latest（d52cf339df65，6.06GB） | **同配方重建**：python 3.12.14 完全一致、mineru 3.4.5 一致、`pip freeze` 125 包**逐包一致（diff 为空）**；体积差源于 arm64/amd64 wheel 编译产物不同 |
| python:3.12-slim（构建基础镜像） | 2c941e860699 | ec7d6c95cd36 | 同 tag；两侧构建出的 python 均为 3.12.14 ✅ |
| 编排文件 | main@v0.27.0+21 的 docker/ + 自定义 mineru overlay | **v0.27.1 官方 docker/** + 同一份 mineru overlay（scp 上传）+ docker-compose-entropy-fix.yml | mineru overlay 逐字一致；v0.27.1 与本地 main 的 compose-base 镜像清单核对一致；entropy-fix 为内核 3.10 熵源修复（§7.4，本地新内核无需） |
| Docker Compose | v5.1.2（Docker Desktop 内置） | v5.5.1（官方 release 二进制） | 同 v5 系，均满足 ≥v2.26.1 |
| 容器内进程形态 | python api server + task executor + nginx（Go server/MCP 默认不启动） | 同 | `API_PROXY_SCHEME` 默认 python 后端；实测 9382-9384 两侧 refused、9381 两侧 404 |
| 服务拓扑/端口/凭据 | 6 容器同网络 ragflow | 一致（UI 端口为有意差异） | API 9380、admin 9381、MCP 9382、go 9383/9384、ES 1200、MySQL 3306、MinIO 9000/9001、Redis 6379、MinerU 8001 全同；UI 本地 80/443、服务器 **8080/8443**（腾 80 给质检智能体，§10.3） |
| .env 差异 | RAGFLOW_IMAGE=v0.27.0 | RAGFLOW_IMAGE=v0.27.1（出厂默认）+ SVR_WEB_HTTP(S)_PORT=8080/8443 + COMPOSE_FILE（含 entropy-fix） | 3 处差异均为有意：①"线上用最新"要求 ②腾 80 给质检智能体（§10.3） ③内核 3.10 熵源修复（§7.4）；MINERU_APISERVER/MINERU_BACKEND 两侧同值 |

**mineru 镜像逐包一致性验证方法**（可复跑，实测结果：125 包无差异）：

```bash
# 本地导出（注意 LC_ALL=C 统一排序规则，否则 macOS/Linux sort 大小写序不同会产生假 diff）
docker exec mineru pip freeze | LC_ALL=C sort > /tmp/local-freeze.txt
# 服务器导出
ssh root@182.92.121.228 'docker run --rm ragflow-mineru:latest pip freeze' | LC_ALL=C sort > /tmp/server-freeze.txt
diff /tmp/local-freeze.txt /tmp/server-freeze.txt && echo IDENTICAL
```

---

## 12. 运维附录

### 12.1 常用命令

```bash
cd /opt/ragflow/docker
# .env 已设 COMPOSE_FILE=三合一（§7.4），裸命令即可；显式 -f 必须带全 3 个文件
# 状态
docker compose ps
# 停止 / 启动 / 重启
docker compose down
docker compose up -d
docker compose restart ragflow-cpu
# 日志
docker compose logs -f ragflow-cpu
docker logs -f mineru
# RAGFlow 服务日志文件（挂载在宿主机）
ls /opt/ragflow/docker/ragflow-logs/
```

### 12.2 MinerU 首次解析预热（重要）

- 镜像**不含模型权重**；首次 `/file_parse` 时 pipeline 后端自动从 ModelScope（`MINERU_MODEL_SOURCE=modelscope`）下载 MinerU2.5-Pro-2605-1.2B + PDF-Extract-Kit 权重（实测缓存 **2.4 GB**），落盘 `docker/mineru-models/`（容器重建不重复下载）。
- **本服务器已于 2026-09-10 完成预热**：ModelScope 国内源实测 ~200MB/s，下载+解析一页测试 PDF 全程 41s（其中推理 ~8s），`warmup_result.json` 记录 `"status":"completed","backend":"hybrid-engine"`。
- CPU、batch=1 下每页约 5–10s；追求速度需 GPU 或外接 vLLM（`MINERU_BACKEND` 改 vlm-http-client 等）。
- 预热命令（在任意宿主机路径放一个测试 PDF）：
  ```bash
  # 用镜像自带 reportlab 生成最小测试 PDF
  docker exec mineru python3 -c "from reportlab.pdfgen import canvas; c=canvas.Canvas('/tmp/test.pdf'); c.drawString(100,750,'warmup'); c.showPage(); c.save()"
  docker cp mineru:/tmp/test.pdf /opt/ragflow/test.pdf
  curl -F files=@/opt/ragflow/test.pdf http://localhost:8001/file_parse
  # 不带 return_* 参数时返回 JSON 任务结果而非 zip，属正常
  ```

### 12.3 平台侧对接（QualityCheckingAgent .env 改法）

服务器部署完成后，按质检智能体的部署位置改平台 `.env`：

```ini
# 质检智能体部署在同一台服务器（推荐；零额外安全组开放，§10.3）：
RAGFLOW_BASE_URL=http://localhost:9380
MINIO_ENDPOINT=localhost:9000
DATABASE_URL=mysql+aiomysql://qc_agent:<平台专用密码>@localhost:3306/quality_inspection   # 共用 RAGFlow 的 MySQL 实例；驱动为 aiomysql（平台异步 SQLAlchemy，旧稿 pymysql 系笔误）；专用账号建法见 deploy/sql/create_platform_user.sql，勿用 root
CHECKPOINTER_URL=postgresql://...@localhost:5432/...   # PG 另行部署，同机 localhost 访问
API_PORT=80                    # 质检智能体跑 80（项目默认 8000；服务器 80 已腾出）
RAGFLOW_API_KEY=<在服务器 RAGFlow UI（http://182.92.121.228:8080）重新生成>   # 旧 key 属本地实例，不通用

# 平台暂留本地 Mac（过渡期）：
RAGFLOW_BASE_URL=http://182.92.121.228:9380    # 需安全组放行 9380（建议限源 IP）
MINIO_ENDPOINT=localhost:9000                  # MinIO 继续用本地侧；服务器 9000 不放行，远程用 SSH 隧道
```

> 新 RAGFlow 实例是空库：数据集需重建，`config/rules/ragflow_chat.yaml` 的 dataset_ids 需同步更新。Web UI 首次登录用自动创建的默认管理员 **admin@ragflow.io / admin**（登录后立即改密码），API Key 在 UI「API」页生成。

### 12.4 已知坑位速查

| 现象 | 原因/处理 |
| --- | --- |
| es01 反复退出，日志 `max virtual memory areas vm.max_map_count [65530] is too low` | §4 的 sysctl 未生效：`sysctl -w vm.max_map_count=262144` 后重启 es01 |
| 拉镜像报 `dial tcp ... i/o timeout` | Docker Hub 直连被墙：确认 `/etc/docker/daemon.json` mirrors 后 `systemctl restart docker` |
| `docker compose` 报 `include` 不支持 | compose 版本过旧（<2.20）：按 §6 装 v5.5.1 |
| ragflow 容器报 MinerU not configured | `.env` 未写 `MINERU_APISERVER=http://mineru:8001`（代码无默认值），改后 `up -d` 重建 ragflow 容器 |
| RAGFlow UI 选了 MinerU 解析仍失败 | 逐 document PATCH `parser_config.layout_recognize="mineru-from-env@mineru-from-env@MinerU"`（平台代码 `app/knowledge/ragflow.py` 已内置该处理） |
| 外网访问不了 80/9380 | 阿里云控制台安全组未放行对应端口（见 §10.3）；服务器本机 iptables INPUT 默认 ACCEPT，docker 自行管理 FORWARD/NAT |
| 磁盘涨得快 | `docker system df` 检查；镜像约 16G+，模型缓存 2.4G，日志已限 100m×3/容器 |
| 构建容器里 apt-get 卡死（宿主机 `curl -I` deb.debian.org 却"通"） | `-I` 只拉 header 掩盖了国际线路慢（实测宿主机大文件仅 29kB/s）→ Dockerfile 里 sed 把 apt 源换成内网 mirrors.cloud.aliyuncs.com/debian |
| ECS 上公网 mirrors.aliyun.com/pypi 只有 ~244kB/s | 换 ECS **内网**源 mirrors.cloud.aliyuncs.com/pypi（实测 ~600MB/s；http 协议需 pip `--trusted-host`） |
| ssh 远程起后台任务导致会话挂住不退出 | `setsid nohup cmd > log 2>&1 < /dev/null &`（三个重定向齐全 + setsid 脱离会话） |
| `pkill -f "docker build"` 把自己的 ssh shell 杀了（exit 255 无输出） | pkill -f 会匹配到自身命令行，用括号技巧：`pkill -f "docker buil[d]"` |
| Web 登录 `/v1/user/login` 404 | v0.27 实际登录端点是 `POST /api/v1/auth/login`（本地同路径行为一致，非部署问题） |
| 静态 docker 包 `docker build` 走 legacy builder（无 BuildKit 日志样式） | 静态 tgz 不含 buildx 插件；简单 Dockerfile 无影响，需 BuildKit 特性时另装 buildx 二进制 |
| 登录 500 `Fail to crypt password` + 日志 `Fatal glibc error: cannot get entropy for arc4random` | glibc 2.39 的 getentropy/arc4random 依赖 getrandom(2)（内核 ≥3.17），3.10 内核没有 → entropy shim + `/etc/ld.so.preload` 挂载（§7.4）。**降级 v0.27.0 无效**（同 Ubuntu 24.04 底） |
| 明文 curl 登录 500 `Fail to crypt password` | 正常语义：前端必须用 public.pem 做 RSA 加密后再提交（§7.4 有加密测试脚本）；本地行为一致 |
| 重建 ragflow 容器后登录又坏 | 显式 `-f` 漏带 entropy-fix overlay（`-f` 覆盖 COMPOSE_FILE）→ 裸 `docker compose up -d` 或带全 3 个文件 |
| 质检智能体要用 80 端口 | RAGFlow UI 已迁 8080/8443，80/443 空闲（§10.3 端口规划表）；平台 `.env` 写 `API_PORT=80` |

### 12.5 完整重装速查（一行流程回顾）

扩容 xfs → sysctl（max_map_count/ip_forward）→ 阿里云静态包 docker-26.1.4 + 3 个 systemd 单元 + daemon.json(mirrors) → ghfast 拉 compose v5.5.1 → codeload 拉 ragflow v0.27.1 tarball 取 docker/ → .env 启用 MINERU 两行 + UI 端口 8080/8443 + 上传 mineru overlay → 编译 entropy shim 并写 entropy-fix overlay + COMPOSE_FILE（§7.4，内核 3.10 必做）→ freeze 版 requirements + Dockerfile 构建 ragflow-mineru → `docker compose up -d` → 验证 §10（登录须用 RSA 加密方式）。
