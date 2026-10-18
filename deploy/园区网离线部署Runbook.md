# 园区网离线部署 Runbook —— 智能质检 Agent 平台（纯内网 · 无互联网）

> 适用：下周向所内园区网的正式部署。园区网**无任何互联网出口**；所内已有 RAGFlow 实例
> （平台经 API 接入）与模型网关（OpenAI 兼容协议）；**目标机为中科方德 NFC Server 4.0（x86_64，
> glibc 2.28）**，已定策：平台运行库 MySQL **本机部署**（所内达梦不适配、转后续正式议题，
> 决策 50 R1：唯一运行库＝MySQL），对象存储用**所内 Ceph（S3 协议，minio SDK 直连）**；
> **目标机禁止安装 Docker**（已确认）——全形态免容器：MySQL 官方 minimal tarball 裸装、
> 金相裸装、备份 mc 静态二进制。
> 基线形态沿用公网验证环境 B′（[`README.md`](README.md)），**平台监听端口改为 18000**（本智能体
> 可用端口段 18000–18099，园区网无 80/低端口权限）：平台裸机 venv + systemd 绑 18000（高位端口
> 免低端口提权），六工具裸机 18011–18016（同机回环，不占园区网放行段；见 §四）；金相在 4.0 裸装（唯一路线，容器旁路已废）；
> 本文只写**差异与离线特化动作**，通用机制（systemd 版本差异、能力热更、端点切换）不再重复，直接查 README 对应章节。

## 0. 与验证环境（B′）的差异总览

| 项 | 公网验证环境 | 园区网部署 | 影响 |
|---|---|---|---|
| 平台 / 六工具形态 | 裸机 + systemd | 不变 | 无 |
| 平台监听端口 | 绑 80（低端口，非 root 需 setcap/AmbientCapabilities 提权） | **绑 18000**（本智能体可用段 18000–18099；高位端口免提权） | install_server.sh 走免提权快路径；防火墙放行段见 §四 |
| 前端 | 开发时 Vite dev server 代理 | **工作区顶层 Vue3 深蓝版**（独立仓 vision-agent-ui），`make_offline_bundle.sh` 构建后植入后端托管的 `frontend/dist`，**平台 18000 端口同源直出** | 同源相对 `/api/v1` + history 模式，无需单独 Nginx；须配 `AUTH_*` 登录鉴权 |
| 目标机 OS | NFS 3.1（glibc 2.17 / systemd 219） | **NFC 4.0（glibc 2.28，预期 systemd ≥ 229）** | 金相可裸装；平台改绑高位端口 18000（免低端口提权）；OpenSSL ≥1.1.1 则免自建 |
| Docker | 验证机有（RAGFlow 套件 / MySQL 容器 / 备份 mc 容器） | **禁止安装** | MySQL 改官方 tarball 裸装；金相容器旁路作废；备份 mc 改静态二进制 |
| RAGFlow | 同机 Docker localhost | **所内既有实例，跨机 API 接入** | `.env` 改 IP + 所内新生成 API Key |
| MySQL | 复用 RAGFlow 套件容器 | **部署机本机裸装**（官方 minimal tarball；禁 Docker；达梦不适配已定策） | 建库/账号/备份均宿主执行，无容器依赖 |
| MinIO/对象存储 | 复用 RAGFlow 套件 silo | **所内 Ceph（S3 协议）** | minio SDK 直连；凭据若无法建桶则请所内预建 `qc-media-assets` |
| 大模型 | 阿里云 MaaS（公网） | **所内模型网关** | `.env` LLM 段显式覆盖，禁留公网默认 |
| Python 依赖安装 | ECS 内网 PyPI 镜像（在线 uv sync） | **离线制品包**（venv 迁移为主，wheels 备灾） | `install_server.sh` 新增 `OFFLINE_VENV_TGZ` / `OFFLINE_WHEELS_DIR` |
| LangSmith 追踪 | 关闭 | 关闭（外网必不可达） | 无 |

## 一、出发前：离线制品包（T-3 日内完成）

在**开发机**与**验证机**分两步执行 [`scripts/dev/make_offline_bundle.sh`](../scripts/dev/make_offline_bundle.sh)：

```bash
# 1) 开发机（Windows Git Bash / WSL，有网）：构建顶层 Vue3 前端 + 源码包 + 锁定清单 + wheels + Python/OpenSSL tarball
#    默认 WEB_SRC=工作区顶层 frontend/（Vue3 深蓝版），其 dist 会被植入 quality-agent/frontend/dist（后端实际同源托管路径）；
#    若交付 quality-agent 自带前端则传 WEB_SRC=quality-agent/frontend。
cd quality-agent && bash scripts/dev/make_offline_bundle.sh

# 2) 验证机（NFS 3.1，与目标机 glibc 同代）：整 venv 迁移包（主路径）+ MySQL 裸装介质/备份工具
bash scripts/dev/make_offline_bundle.sh --on-verifier --with-mysql
#    （不再 --with-image：目标机禁 Docker，金相镜像 tar 无用途）
```

产物汇入一个 `offline-bundle/` 目录（随附 `SHA256SUMS.txt`），拷入 U 盘/移动硬盘：

| 制品 | 必要性 | 说明 |
|---|---|---|
| `source.tar.gz` | **必需** | 含 `frontend/dist`（已植入顶层 Vue3 深蓝版产物）、uv.lock、deploy/、scripts/；**不含 .env** |
| `qcagent-venv.tar.gz` | **必需（主路径）** | 验证机全量测试通过的整 venv；目标机须同为 `/usr/local/bin/python3.11` + glibc 同代，且 **APP_DIR 同为 /opt/agent/vision-agent/quality-agent**（venv 脚本 shebang/pyvenv.cfg 路径硬编码） |
| `wheels/` + `requirements-offline.txt` | 备灾 | venv 迁移失败时 `pip --no-index` 重装用 |
| `Python-3.11.14.tar.xz` / `openssl-1.1.1w.tar.gz` | 视目标机 | 无 3.11 时 `install_python311.sh PY_TARBALL=… OPENSSL_TARBALL=…` 离线编译 |
| `mysql-8.0.x-…-glibc2.17-x86_64-minimal.tar.xz` + `mc`（静态二进制） | **必需** | 本机 MySQL 裸装（禁 Docker）+ 备份工具，`make_offline_bundle.sh --with-mysql` 下载；**不再带任何镜像 tar** |
| `libaio` RPM（视机） | 视目标机 | MySQL 裸装唯一系统依赖；演练 VM 实跑时实测，缺则制品包补带 RPM |
| 六工具交付包（`六工具上交API/`） | **必需** | 本就离线（`install_offline.sh --no-index`），原样带入 |

**出发前预演（强制）**：在本地 NFC 4.0 VM 按 §七 做全程断网演练（即计划中的模拟离线全路径），
确认制品包无遗漏、分支行为符合预期后再进园区网。

### 向所内提前的《环境信息与权限申请单》

- [ ] 部署机：IP、OS 版本（`cat /etc/os-release`，预期 NFC Server 4.0）、glibc（`ldd --version`）、是否已装 Python 3.11、是否有 libaio（MySQL 裸装依赖）
- [ ] **Docker 禁用确认**（已确认禁止）：全部署形态免容器——MySQL 裸装 tarball、金相裸装、备份 mc 静态二进制；相关容器制品一律不纳入申请
- [ ] **MySQL 本机部署授权**：部署机本机裸装 MySQL 8（官方 tarball，不碰系统仓库，介质随制品包带入）；
      所内达梦不适配（决策 50 R1），如被追问则发本节 §0 引用 + 后续正式改造议题立项
- [ ] RAGFlow：API 地址（默认 9380）+ 管理 UI 地址；**开通 API Key 权限**（UI「API」页自行生成）
- [ ] Ceph 对象存储：RGW endpoint（host:port）+ 专用账号；**确认该账号能否建桶**，不能则请所内预建
      `qc-media-assets` 并授予读写；HTTPS 自签证书情况（决定 `MINIO_SECURE`）
- [ ] 模型网关：base_url（OpenAI 兼容 `/v1`）、密钥、文本模型名、视觉模型名（无 VLM 则 agent 轨置灰，如实记录）
- [ ] 网络：园区网用户对平台机 **18000** 放行（本智能体可用端口段 18000–18099）；平台机→所内 RAGFlow 9380 / Ceph 端点 / 模型网关的出方向放行；时钟同步（NTP 内网源或校时制度）

## 二、现场执行顺序（目标机 root）

### Step 1 基线核对（逐项抄入部署记录，格式参照 `records/baseline_*.md`）

```bash
uname -m; cat /etc/os-release; ldd --version | head -1        # glibc 2.17/2.28 → 决定金相形态与 venv 迁移可行性
python3.11 -V || echo "无 3.11 → 走 PY_TARBALL 离线编译"
systemctl --version | head -1                                  # 仅绑低端口(80)时 <229 才需 setcap；园区网 18000 高位端口免提权、与版本无关
ldconfig -p | grep libaio || echo "缺 libaio → MySQL 裸装起不来，制品包补带 RPM"
ss -ltnp | grep -E ':(18000|3306|1810[1-6])\b' || echo "端口空闲"
date                                                           # 时钟核对（报告时间戳/检查点依赖）
# 到所内服务连通性（端口通 ≠ 服务可用，都要 200/握手成功；MySQL 本机化后无跨机库探活）：
curl -s -o /dev/null -w '%{http_code}\n' http://<所内RAGFlow_IP>:9380/
curl -s http://<Ceph_RGW>:<端口>/ && echo ceph-ok   # S3 端点应答 XML 即连通
curl -s http://<网关IP>:<端口>/v1/models || echo "网关探活失败 → 记录，LLM 段留空降级"
```

### Step 2 环境准备

```bash
# 目标机无 python3.11 时（离线编译，yum 编译依赖需所内源可用；不可用则带入 RPM 包）：
PY_TARBALL=<bundle>/Python-3.11.14.tar.xz OPENSSL_TARBALL=<bundle>/openssl-1.1.1w.tar.gz \
  bash deploy/env-prep/install_python311.sh
PLATFORM_PORT=18000 bash deploy/env-prep/init_ai_tools_env.sh   # 工具账户/目录 + firewalld 放行 18011–18016 与平台 18000（幂等）
```

### Step 3 平台安装（离线主路径）

```bash
mkdir -p /root/QualityCheckingAgent
tar -xzf <bundle>/source.tar.gz -C /root/QualityCheckingAgent
# 离线主路径（venv 迁移）——APP_DIR 缺省已是 /opt/agent/vision-agent/quality-agent，与验证机一致；APP_PORT=18000 对齐端口段：
APP_PORT=18000 OFFLINE_VENV_TGZ=<bundle>/qcagent-venv.tar.gz bash deploy/install_server.sh /root/QualityCheckingAgent
# 备灾路径（venv 自检失败时）：
# APP_PORT=18000 OFFLINE_WHEELS_DIR=<bundle>/wheels OFFLINE_REQUIREMENTS=<bundle>/requirements-offline.txt \
#   bash deploy/install_server.sh /root/QualityCheckingAgent
```

`install_server.sh` 第 3/6 步三分支：`OFFLINE_VENV_TGZ`（解包整 venv，主路径）→
`OFFLINE_WHEELS_DIR`（venv pip `--no-index --find-links` + `requirements-offline.txt`，备灾）→
都不给则在线 uv sync。安装尾部**统一 venv 自检**（fastapi/langgraph/tiktoken/PIL/greenlet 等关键
模块 import），离线包与目标机 glibc 不同代在这一步现形——失败即切换备灾路径，勿硬启动。

### Step 4 配置 `.env`

以 [`env.园区网.example`](env.园区网.example) 为底稿，替换全部 `<...>` 占位后落
`/opt/agent/vision-agent/quality-agent/.env`（`chown qcagent:qcagent`、`chmod 600`）。四个易错点：

1. `LLM_BASE_URL` 必须显式指向所内网关——留空 key 可降级，但填了模型名却回退公网默认地址会空耗超时重试；
2. systemd EnvironmentFile 不支持行内注释（沿用 README §4 规范）；
3. Ceph 的 `MINIO_ENDPOINT` 填 **host:port，不带 `http://` 前缀**（minio SDK 约定）；`MINIO_SECURE=true` 仅当 RGW 开了 HTTPS；
   拿不到凭据就整段留空（降级本地落盘），不要填半截。
4. **登录鉴权 `AUTH_*` 必须覆盖默认口令**：顶层 Vue3 前端带 `/login` 页，后端 `config.py` 的
   `auth_password`/`auth_secret` 是内置随机默认值，园区网生产务必改为自己的强口令（否则前端无法
   登录、且默认密钥泄露可伪造会话）；`AUTH_ENABLED=true`（缺省）。前端靠同源 Cookie 会话，
   只要平台 18000 端口同源直出 dist、`.env` 配好 AUTH_* 与 `API_PORT=18000` 即可，无需额外前端服务。

### Step 5 本机 MySQL 裸装与建库（目标机禁 Docker；达梦不适配，平台库落本机）

MySQL 官方 generic minimal tarball 裸装（自带 OpenSSL 的自包含二进制；glibc2.17 构建在
NFC 4.0 直接可跑；不碰系统包管理器）。唯一系统依赖 libaio：`ldconfig -p | grep libaio`，
缺则先装制品包带的 libaio RPM。

```bash
tar -xJf <bundle>/mysql-8.0.*-linux-glibc2.17-x86_64-minimal.tar.xz -C /usr/local
ln -sfn /usr/local/mysql-8.0.* /usr/local/mysql
groupadd -r mysql; useradd -r -g mysql -s /sbin/nologin mysql
mkdir -p /opt/qc-mysql-data /opt/qc-mysql-run && chown mysql:mysql /opt/qc-mysql-data /opt/qc-mysql-run
/usr/local/mysql/bin/mysqld --initialize-insecure --user=mysql \
  --basedir=/usr/local/mysql --datadir=/opt/qc-mysql-data
```

systemd 单元 `/etc/systemd/system/mysqld.service`（安全边界：只绑 127.0.0.1）：

```ini
[Unit]
Description=MySQL (qc bare install)
After=network.target
[Service]
User=mysql
ExecStart=/usr/local/mysql/bin/mysqld --basedir=/usr/local/mysql --datadir=/opt/qc-mysql-data \
  --socket=/opt/qc-mysql-run/mysqld.sock --bind-address=127.0.0.1 --port=3306
LimitNOFILE=65535
[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now mysqld
# 设 root 口令并留 600 权限文件（备份脚本 MYSQL_ROOT_PASS_FILE 要读）：
/usr/local/mysql/bin/mysqladmin -S /opt/qc-mysql-run/mysqld.sock -uroot password '<强 root 口令>'
printf '%s\n' '<强 root 口令>' > /opt/qc-mysql-root.pass && chmod 600 /opt/qc-mysql-root.pass
# 建库 + 平台账号（host 收敛见 §三）：
/usr/local/mysql/bin/mysql -h 127.0.0.1 -P 3306 -uroot -p'<root口令>' < /opt/agent/vision-agent/quality-agent/scripts/sql/init.sql
vim /opt/agent/vision-agent/quality-agent/deploy/sql/create_platform_user.sql    # 改 <CHANGE_ME> 强密码；host '%' 建议收敛为 'localhost'
/usr/local/mysql/bin/mysql -h 127.0.0.1 -P 3306 -uroot -p'<root口令>' < /opt/agent/vision-agent/quality-agent/deploy/sql/create_platform_user.sql
```

若所内 OS 媒体自带 MySQL 8 server 及其依赖 RPM，也可 `yum localinstall` 形态，
后续平台配置/备份完全一致。

若所内硬性要求入库达梦：见 §五 风险表末行，属架构级改造，不在本次部署范围。

### Step 6 起平台 + 健康检查

```bash
systemctl start quality-agent
journalctl -u quality-agent -f
curl -s http://127.0.0.1:18000/api/v1/health
# 期望 code=0, database=ok；vlm_available 按网关实况（false=agent 轨置灰，如实记录）
```

### Step 7 能力种子 + 端点切换

```bash
cd /opt/agent/vision-agent/quality-agent
runuser -u qcagent -- .venv/bin/python -m scripts.sync_capabilities --no-tasks
# 六工具逐个上真实端点（同机 127.0.0.1；跨机改工具机 IP）——机制同 README §5：
bash deploy/switch_tool_endpoint.sh borescope_defect_detection_v1 http://127.0.0.1:18014 http://127.0.0.1:18014/health
# ……xray(18011) fracture(18012) metallography(18013) hotsection(18015) fluorescence(18016) 同理
```

已知持续 mock 项（如实写入验收记录，不算部署故障）：X 光接口返工中、断口交付包缺失。

### Step 8 六工具部署

- **孔探/热端/荧光**：按各自离线指南（[`孔探离线部署指南.md`](孔探离线部署指南.md) /
  [`热端部件离线部署指南.md`](热端部件离线部署指南.md) / [`荧光渗透离线部署指南.md`](荧光渗透离线部署指南.md)），本就全程离线；
- **金相**：目标机为 4.0（glibc 2.28）且 **禁 Docker → 裸装是唯一路线**（容器化指南 §10 迁移路线：
  `install_offline.sh` 直装 + `ai-tool@metallography` systemd）；容器旁路已作废，
  演练（§七）必须把裸装跑绿；端点命令不变（127.0.0.1:18013）；
- **每个工具双验证**（判真伪看指纹不看 200）：`/health` 指纹字段 + 实打一次 `/api/v1/inference`。

### Step 9 联调验收

- 登录鉴权：用 `.env` 的 AUTH_USERNAME/PASSWORD 能过顶层 Vue3 前端 `/login` 进首页（未配 AUTH_* 或沿用默认口令即为不合格）；
- 前端逐场景 E2E：六场景各上传一张真实图 → 出判级结论 → 报告/产物可打开（SPA 深链刷新不 404）；
- 知识库检索走通所内 RAGFlow（返回所内库内容而非报错降级）；
- 报告解读确认走所内网关：`journalctl -u quality-agent | grep -iE 'timeout|dashscope|aliyuncs'` 应无外网残留；
- 场景确认卡 / VLM 自动识别按 `vlm_available` 实况验收。

### Step 10 备份制度落地

MySQL 已本机化且目标机禁 Docker，备份脚本走**裸装分支**（`MYSQL_MODE=host`，已支持）；
先把制品包内 mc 静态二进制装上（备份 MinIO 桶用，Go 单文件免依赖）：

```bash
install -m 755 <bundle>/mc /usr/local/bin/mc
MYSQL_MODE=host bash deploy/backup_platform_data.sh
# root 口令读 MYSQL_ROOT_PASS_FILE（缺省 /opt/qc-mysql-root.pass，§Step 5 已建）；
# MinIO 步 endpoint 指向所内 Ceph RGW（http://<rgw>:端口），脚本无需改动。
```

首次备份 + 一次恢复演练；建议 cron 与保留策略同 README §6。

## 三、平台账号与暴露面（本机 MySQL 裸装形态）

MySQL 裸装后无 docker-proxy 环节：平台经 TCP 连 127.0.0.1，`qc_agent` 账号 host 建议从
`'%'` 收敛为 `'localhost'`（改 `create_platform_user.sql` 一处；若嫌 127.0.0.1 反向解析
不确定，可同时授 `'127.0.0.1'`）；安全边界由 my.cnf/启动参数 `bind-address=127.0.0.1`
保证，3306 不对园区网暴露。若存在多机分形态（工具机/平台机分离）且 MySQL 需跨机，
再把 host 收敛为指定 IP 并放防火墙。

## 四、网络与防火墙（园区网侧）

- **本智能体可用端口段仅 18000–18099**：对园区网用户仅放行平台机 **18000**（前端 + /api/v1 同源入口）；22 限源（运维跳板机）；
- 平台机出方向放行：→ 所内 RAGFlow 9380、Ceph RGW 端点、模型网关端口（MySQL 已本机化，无跨机数据库流量）；
- 六工具 18011–18016 **已在可用段内**（18000–18099）：同机部署仅绑 127.0.0.1（回环不跨网络、不占放行段，通常免申请）；**若工具机与平台机分离**需跨机访问，18011–18016 仍在许可段内，无需额外申请；
- 平台机**不应有默认路由出园区网**——部署后跑一次 Step 9 的外网残留检查确认。

## 五、风险与预案

| 风险 | 征兆 | 预案 |
|---|---|---|
| 目标机 glibc 与制品代不同 | install_server.sh 尾部 venv 自检 import 崩 | 切 `OFFLINE_WHEELS_DIR` 备灾路径（wheels 平台标签须匹配目标机，`pip debug --verbose` 复核）；金相裸装独立 venv 不受影响 |
| 本机 MySQL 被所内否决（硬性要求入达梦） | 申请单被驳回 | **达梦适配是架构级改造**（异步引擎无成熟达梦驱动、MySQL 方言 DDL 全量重写、备份换 dexp），不在下周交付范围；升级牵头单位协调：要么本机 MySQL 例外，要么延期改造（决策 50 R1 为依据） |
| Ceph 账号无建桶权限 | 首启 `ensure_bucket` 失败、日志 bucket 相关 ERROR | 请所内预建 `qc-media-assets` + 授予读写；代码已有降级（本地落盘），主链路不断 |
| 模型网关非 OpenAI 兼容 / 无 VLM | Step 1 探活失败或 health `vlm_available:false` | LLM 段留空降级（报告退纯模板、agent 轨置灰），主链路照常交付；网关适配后续热改 `.env` + restart |
| 金相 wheel 文件名 ISO9660 截断复发 | 裸装/容器 install 报 `from versions: none` | 容器化指南 §3.2 改名脚本（用包内 SHA256SUMS 权威名按前缀改回） |
| 金相裸装失败（缺系统库/依赖装不上） | `install_offline.sh` 报错或 `ai-tool@metallography` 起不来 | **禁 Docker 后无容器旁路兜底**：wheel 文件名 ISO9660 截断按容器化指南 §3.2 改名预案；仍不通则升级所内由工具方到场支持，验收记录如实登记 18013 状态 |
| MySQL 裸装缺 libaio | mysqld 启动失败（提示 libaio 相关） | 制品包预带 libaio RPM 现场 `rpm -ivh`；演练 VM 已实测该路径 |
| 传输制品缺漏 | 现场找不到 tar | SHA256SUMS 全清单进盘前先 `sha256sum -c`；按 §一 表格逐项打钩 |
| 时钟漂移 | 报告时间戳/检查点异常 | 部署当日人工校时 + 申请内网 NTP；`date` 记入部署记录 |

## 六、日常运维速查（园区网增量）

通用速查见 README §7；园区网额外注意：

| 操作 | 说明 |
|---|---|
| 版本升级 | 开发机重打 bundle → 传盘 → `APP_PORT=18000 OFFLINE_VENV_TGZ=… bash deploy/install_server.sh`（幂等，不覆盖 .env/data）→ restart |
| 换模型/网关 | 编辑 `.env` LLM 段 → `systemctl restart quality-agent`（无代码改动） |
| RAGFlow 不可达 | 知识层自动降级，主链路不受影响；恢复后无需重启 |
| 备份 | §Step 10：`MYSQL_MODE=host` 裸装分支；mc 为宿主静态二进制 |

## 七、本地 NFC 4.0 VM 全程断网演练（出发前强制，两阶段）

> 目的：用与目标机同代的 OS（NFC 4.0）验证制品包完整性与离线链路；顺带把 README §9
> 预期的“4.0 分支”（平台绑高位端口 18000 免提权、金相裸装、OpenSSL 免自建）首次实跑验证。
> 演练记录归档 `deploy/records/`，发现的问题回写本文。

### 阶段 A：VM 联网准备（一次性）

1. 快照先打（回退保险）；上传仓库源码 + `install_python311.sh` 所需 tarball；
2. 采集基线并归档：`bash deploy/env-prep/collect_baseline.sh`（`verify_acceptance.sh` 项 1 期望值
   `EXPECT_OS="NFC Server 4.0"`，此次应真实 PASS 而非差异记录）；重点登记：glibc、systemd 版本
   （≥229 则走 AmbientCapabilities）、OpenSSL（≥1.1.1 则 install_python311 自动跳过自建分支）；
3. `install_python311.sh`（联网模式）装好解释器；**不装 docker**（对齐园区网禁令，让演练全程走免容器路径）；
4. 在 VM 上构建“VM 自己的主路径 venv”（与未来园区网目标机同代同路径，这才是有效的迁移制品）：
   源码放 `/root/QualityCheckingAgent` → `APP_PORT=18000 bash deploy/install_server.sh`（在线 C 分支，VM 走 NAT 可达镜像源；端口对齐园区网实态）；
5. 在 VM 上跑 `make_offline_bundle.sh --on-verifier --with-mysql`
   （产出 qcagent-venv.tar.gz + MySQL tarball/mc/minio-server 静态二进制汇入 bundle；开发机产的
   source.tar.gz/wheels/tarball 同步拷入 VM 汇入）；
6. 六工具交付包、测试图一并拷入 bundle。

### 阶段 B：断网执行（模拟园区网）

1. **关机 → 移除/断开全部网卡（NAT+_hostonly）→ 开机**；断网确认：
   `curl -m 3 https://www.baidu.com` 必失败，`ip route` 无默认路由；
2. 从共享文件夹/虚拟磁盘把 bundle 全量拷入 VM 本地目录（共享文件夹不等于网络，可保留）；
3. 按 §二 Step 1–10 完整执行，所内四要素的演练替身：
   - MySQL → 本机裸装 tarball（与园区网目标形态完全一致，Step 5 照跑）；
   - RAGFlow → 留空降级（验收点：主链路不阻断、日志无刷级 ERROR）；
   - Ceph → 本机 minio server 静态二进制当 S3 RGW 替身（Go 单文件直跑，免 Docker；验收点：minio SDK 对非
     MinIO 后端的建桶/上传/预签名 URL 同样工作；拿不到替身二进制则留空降级验一回）；
   - 模型网关 → 留空降级（验收点：health `vlm_available:false`、前端 agent 轨置灰、报告退模板不报错）；
4. 4.0 专属验证点（园区网同机会触发，首次实跑）：
   - 平台绑 18000（高位端口）：非 root 直接可绑，`journalctl` 无 Errno 13、无 setcap（install_server.sh 走免提权快路径）；
   - 金相裸装（容器化指南 §10 命令）+ `ai-tool@metallography` systemd（**唯一路线，演练必须跑绿**；容器旁路已作废）；
   - uv.lock 四约束包（tiktoken/pillow/greenlet 等）在 2.28 下 import 正常（约束版本在新一代同样成立）；
5. 前端 E2E（浏览器用 host-only 网段内的宿主机访问 `http://<VM_IP>:18000/`）：先验**登录**（用 `.env` 的
   AUTH_USERNAME/PASSWORD 过 `/login` → 首页可进）+ SPA history 直连刷新不 404（catch-all 回 index.html）；
   六场景上传验主链路；若无图形浏览器则用 VM 内 curl 先 `POST /api/v1/auth/login` 拿会话 Cookie、再仿 multipart 提交验主链路；
6. 备份演练：`MYSQL_MODE=host bash deploy/backup_platform_data.sh` + 恢复到临时库；
7. 产出：§八 格式的记录归档 `deploy/records/rehearsal_nfc40_<日期>.md`，问题回写本文与脚本。

### 演练通过标准

Step 1–10 全绿（除 RAGFlow/网关降级替身项）；断网全程无任何联网动作发生（网络适配器保持移除，
出现“解析失败/超时”类日志即为遗漏联网点，逐个登记并修复）。

## 八、验收记录模板（演练/现场填写后归档 `deploy/records/`）

```
部署日期：____  部署机：____（OS/glibc/systemd/libaio 四项基线）
制品包校验：sha256sum -c 全部 OK（是/否，缺漏项：____）
依赖安装路径：离线A venv 迁移 / 离线B wheels（自检模块清单通过情况：____）
所内接入：RAGFlow（检索样例：____）/ MySQL / MinIO（授权/降级）/ 模型网关（模型：____，vlm_available：____）
六工具：孔探__/热端__/荧光__/金相__（真实，指纹+实打）；X光 mock（接口返工）；断口 mock（缺交付包）
E2E：六场景出结论（是/否）；外网残留检查（通过/未过）
备份：首次备份 + 恢复演练（是/否）
遗留问题与责任方：____
```
