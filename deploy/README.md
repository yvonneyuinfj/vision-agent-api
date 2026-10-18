# 服务器部署 Runbook —— 智能质检 Agent 平台（非容器化 · B′）

> 对应《公网云服务器验证环境准备》与四点决策：
> ① 4.0 服务器后到（先在现有机器演练）；② 非容器化范围 = 非 RAGFlow 套件（平台与算法工具裸机，
> RAGFlow/MinerU 及其 MySQL/ES/MinIO 维持 Docker）；③ Python 3.11 统一基线（本地与服务器一致）；
> ④ 算法工具 18011–18016 直连，平台复用 RAGFlow 套件的 MySQL/MinIO。

## 0. 部署形态总览

```
中科方德验证服务器（x86_64）
├── Docker（仅 RAGFlow 套件）：ragflow 9380 / mysql 3306 / minio(silo) 9000/9001 / es / valkey / mineru
├── 裸机 · 平台：/opt/agent/vision-agent/quality-agent（qcagent 账户，venv Python 3.11 + systemd，端口 80）
│     └── DATABASE_URL → localhost:3306（RAGFlow MySQL，独立库 quality_inspection + 专用账号 qc_agent）
│     └── MINIO_ENDPOINT → localhost:9000（RAGFlow MinIO，独立桶 qc-media-assets）
└── 裸机 · 算法工具：/opt/agent/vision-agent/tools/<tool>（各单位独立账户 + venv，端口 18011-18016 直连）
```

安全组：对外仅放行 80（平台）与 22（限源）；3306/6379/1200/9000/9001 一律不放行
（部署手册 §10.3）；18011-18016 仅对联调来源 IP/内网开放。

## 1. 前置条件

1. 环境准备完成：`deploy/env-prep/`（Python 3.11、工具目录/账户/端口，见该目录 README）；
2. RAGFlow 套件健康（部署手册 §10 验证表：mysql/minio healthy）；
3. 本地构建前端产物：`cd frontend && npm ci && npm run build`（**dist 不入 git，服务器无 node，必须随源码上传**）；
4. 源码上传服务器：`rsync -av --exclude .venv --exclude .git --exclude node_modules ./ root@<服务器IP>:/root/QualityCheckingAgent/`
   （frontend/dist 必须在内）。

## 2. 安装（root）

```bash
cd /root/QualityCheckingAgent
bash deploy/env-prep/install_python311.sh      # 已装过可跳过（幂等）
bash deploy/env-prep/init_ai_tools_env.sh      # 工具目录/账户/端口（幂等）
bash deploy/install_server.sh /root/QualityCheckingAgent
```

`install_server.sh` 完成：qcagent 账户（无 sudo）→ 代码同步到 `/opt/agent/vision-agent/quality-agent` →
Python 3.11 venv + 依赖（ECS 内网 PyPI 源）→ 放置 `.env` 模板（600 权限）→ systemd 单元
（`quality-agent.service`，CAP_NET_BIND_SERVICE 绑 80）。**不会自动启动**。

## 3. 数据库与对象存储（复用 RAGFlow 套件）

```bash
# 1) 建库建表（init.sql 自建 quality_inspection 库，utf8mb4）
docker exec -i docker-mysql-1 mysql -uroot -p'<root密码>' < /opt/agent/vision-agent/quality-agent/scripts/sql/init.sql

# 2) 平台专用账号（勿用 root；先编辑 SQL 里的 <CHANGE_ME> 强密码）
vim /opt/agent/vision-agent/quality-agent/deploy/sql/create_platform_user.sql
docker exec -i docker-mysql-1 mysql -uroot -p'<root密码>' < /opt/agent/vision-agent/quality-agent/deploy/sql/create_platform_user.sql
```

- MinIO 无需初始化：平台首启自动建桶 `qc-media-assets`；凭据 = RAGFlow compose 的 MinIO 账密（手册 §7.2）。
- ⚠️ 驱动勘误：`DATABASE_URL` 必须用 **`mysql+aiomysql://`**（异步驱动；手册 §12.3 旧稿 `pymysql` 系笔误，已勘误）。
  账号 host 用 `'%'`（docker-proxy 转发后 MySQL 看到的源地址是网桥网关，非 localhost），3306 不出安全组，风险可控。

## 4. 配置与启动

```bash
vim /opt/agent/vision-agent/quality-agent/.env        # 改所有 <...> 占位：DB 密码 / RAGFLOW_API_KEY / MINIO 凭据
systemctl start quality-agent
journalctl -u quality-agent -f     # 观察启动
curl -s http://127.0.0.1/api/v1/health   # 期望 code=0, database=ok
```

`.env` 注意：systemd EnvironmentFile **不支持行内注释**（`KEY=value # 注释` 会把注释并入值），
模板已按此规范书写，修改时保持。

## 5. 能力注册与端点切换（联调期运维动作，无代码改动）

能力事实源是 `capability_registry` 表（15s 轮询热生效）。首次注册种子（mock 端点）：

```bash
cd /opt/agent/vision-agent/quality-agent
runuser -u qcagent -- .venv/bin/python -m scripts.sync_capabilities --no-tasks
```

各工具进场后，把 endpoint 从 mock 切到真实服务（同机用 127.0.0.1；跨机用验证机 IP）：

```sql
UPDATE capability_registry
   SET endpoint='http://127.0.0.1:18014',
       health_endpoint='http://127.0.0.1:18014/health',
       version=version+1
 WHERE capability_id='borescope_defect_detection_v1';
```

或调 `POST /api/v1/capabilities/reload` 立即生效。**工具 ↔ 能力映射表**（内部 scene 编码不改名，
对外名按验证文档表2）：

| 工具（对外） | 目录 | 端口 | capability_id | scene |
|---|---|---|---|---|
| X 射线 xray | /opt/agent/vision-agent/tools/xray | 18011 | `dr_defect_detection_v1` | dr |
| 断口 fracture | /opt/agent/vision-agent/tools/fracture | 18012 | `fracture_defect_detection_v1` | fracture |
| 金相 metallography | /opt/agent/vision-agent/tools/metallography | 18013 | `metallography_defect_detection_v1` | metallography |
| 孔探 borescope | /opt/agent/vision-agent/tools/borescope | 18014 | `borescope_defect_detection_v1` | borescope |
| 热端部件 hotsection | /opt/agent/vision-agent/tools/hotsection | 18015 | `infrared_defect_detection_v1` | infrared |
| 荧光 fluorescence | /opt/agent/vision-agent/tools/fluorescence | 18016 | `fpi_defect_detection_v1` | fpi |

回退 mock（演示/自测）：把 endpoint UPDATE 回 mock 地址即可，同一套热生效机制。

> 📦 **glibc 2.28 工具（如金相 18013）在 2.17 主机裸装失败时，走容器旁路部署**：见 [`deploy/容器化离线部署指南.md`](容器化离线部署指南.md)。平台侧仍用本节 `switch_tool_endpoint.sh` 切端点（工具绑 127.0.0.1，容器/裸机对平台透明），无需改代码。
>
> 🔧 **裸机离线部署**：孔探→ [`deploy/孔探离线部署指南.md`](孔探离线部署指南.md)；热端部件→ [`deploy/热端部件离线部署指南.md`](热端部件离线部署指南.md)；荧光渗透→ [`deploy/荧光渗透离线部署指南.md`](荧光渗透离线部署指南.md)。
>
> 🏢 **园区网（纯内网、无互联网）正式部署**：总纲见 [`园区网离线部署Runbook.md`](园区网离线部署Runbook.md)（离线制品包准备 / install_server.sh 离线分支 / 目标机 NFC 4.0 且**禁装 Docker**（MySQL 官方 tarball 裸装、金相裸装、mc 静态二进制，不带任何容器镜像制品）；所内接入定策：RAGFlow·模型网关跨机 API，**MySQL 本机部署**（达梦不适配，决策 50 R1），对象存储所内 Ceph 走 S3 协议；**平台监听端口改 18000**（本智能体可用段 18000–18099，高位端口免低端口提权；与 §9 的绑 80 分支互斥）；含 §七 VM 断网演练章）；配置模板 [`env.园区网.example`](env.园区网.example)，制品脚本 `scripts/dev/make_offline_bundle.sh`，演练记录模板 [`records/rehearsal_nfc40_template.md`](records/rehearsal_nfc40_template.md)。

## 6. 备份制度（强制）

平台数据寄生在 RAGFlow 套件内（生命周期耦合）。**RAGFlow 升级 / compose 重建 / 服务器迁移 /
任何 `docker compose down -v` 之前**，必须执行：

```bash
bash deploy/backup_platform_data.sh     # mysqldump 全库 + mc mirror 桶 → /opt/backups/quality-agent/<时间戳>/
```

- 建议联调期每日 cron：`0 3 * * * root bash /opt/agent/vision-agent/quality-agent/deploy/backup_platform_data.sh`
- 保留最近 7 份（`KEEP=` 可调），带 SHA256 清单；恢复命令在脚本输出里。
- 演练要求：首次备份后做一次恢复演练（恢复到临时库验证 dump 可用）。

## 7. 日常运维速查

| 操作 | 命令 |
|---|---|
| 启停/重启 | `systemctl start|stop|restart quality-agent` |
| 日志 | `journalctl -u quality-agent -f`；应用日志 `logs/` |
| 改配置 | 编辑 `.env` → `systemctl restart quality-agent` |
| 能力热更 | DB `UPDATE ... version=version+1`（15s 生效）或 `POST /api/v1/capabilities/reload` |
| 版本升级 | 本地构建 dist → rsync 源码 → `bash deploy/install_server.sh`（幂等，不覆盖 .env/data）→ restart |
| 验收自检 | `bash deploy/env-prep/verify_acceptance.sh`（PORT_MODE=listen 可在部署后全链路探测） |

## 8. 故障排查

| 现象 | 原因/处理 |
|---|---|
| 启动即退，日志 `Permission denied ... :80` | systemd <229：`AmbientCapabilities` 被忽略且 nnp 废掉 setcap → 见 §9（install_server.sh 已自动处理）；或改 `API_PORT>1024` |
| MySQL 认证失败 `caching_sha2_password` | 缺 cryptography？平台依赖已含；确认账号按 `create_platform_user.sql` 建（host='%'） |
| `ModuleNotFoundError: pymysql` 或 URL 报错 | DATABASE_URL 驱动段必须是 `mysql+aiomysql`，不是 pymysql |
| 首页 404 / 无静态资源 | `frontend/dist` 未随 rsync 上传（git 不含 dist；本地 `npm run build` 后重传） |
| RAGFlow 相关报错但主链路可用 | RAGFlow 不可达时知识层降级；检查 `docker ps` 与 RAGFLOW_API_KEY 是否为服务器实例新生成 |
| pip 安装极慢 | 用了公网 PyPI（~244kB/s）；改 ECS 内网源 mirrors.cloud.aliyuncs.com（install_server.sh 默认已用） |
| 能力切了 endpoint 不生效 | 确认 `version=version+1`（轮询按 MAX(version) 比对）；或手动 reload |

## 9. systemd 版本差异（非 root 绑 80 的两条路）

`AmbientCapabilities` 需要 **systemd ≥ 229**。在 systemd 219（CentOS 7 代基座，如
NFS Server 3.1）上该指令是 `Unknown lvalue` 被静默忽略，而同单元的
`NoNewPrivileges=true` 会让内核忽略 setcap 文件能力，结果是非 root 绑 80 必然
`Errno 13 permission denied` 并进入 `Restart=on-failure` 循环。

`install_server.sh` 第 5 步按 `systemctl --version` 自动二选一：

| systemd | 绑 80 机制 | NoNewPrivileges | 装机动作 |
|---|---|---|---|
| ≥ 229（NFC Server 4.0 预期） | `AmbientCapabilities=CAP_NET_BIND_SERVICE` | 保留（加固完整） | 无额外动作 |
| < 229（NFS 3.1 实测 219） | `setcap cap_net_bind_service+ep` 于真实解释器 | **自动注释** | `setcap` + sed 注释单元该行 |

两条路都保留 `CapabilityBoundingSet=CAP_NET_BIND_SERVICE`，即该服务进程树的能力上限
始终只有低端口绑定一项。setcap 路线的残余风险：① 失去 nnp 对 setuid exec 提权的
拦截（本机 su/sudo 对无口令无 sudoers 条目的 qcagent 无用；pkexec 类即使得手，
bounding set 仍锁死能力，euid 0 而无 CAP_DAC_OVERRIDE/CAP_SETUID 价值极低）；
② setcap 作用于共享的 `/usr/local/bin/python3.11`，本机所有用该解释器的账户进程
都能绑 <1024 端口（六工具账户均为受控内部账户，80/443 本就为平台预留）。

回滚：恢复单元该行 → `systemctl daemon-reload && systemctl restart quality-agent`；
彻底还原再执行 `setcap -r /usr/local/bin/python3.11`。

**迁移到 NFC Server 4.0 时**：`AmbientCapabilities` 原生生效，应恢复
`NoNewPrivileges=true`（install_server.sh 在新机上不会注释它），并可
`setcap -r` 清掉文件能力，回到更完整的加固姿态；本节差异按部署手册 §八 记入变更管理。

### 其他实跑差异项（2026-09-14，NFS Server 3.1）

| 项 | 手册预期 | 实测 | 处置 |
|---|---|---|---|
| 操作系统 | NFC Server 4.0 | NFS Server 3.1（RTM4-U1，CentOS 7 代，glibc 2.17 / gcc 4.8.5） | 按用户决定先行部署，4.0 到位后重跑全套 |
| MinIO 凭据 | §7.2 出厂 `infini_rag_flow` | access 长度 8 / secret 长度 15（非对称） | 以 `docker exec docker-minio-1 printenv` 为准；实测可建桶 |
| 依赖解析 | pip 自由安装 | glibc 2.17 下新版 tiktoken/pillow/greenlet 仅发 manylinux_2_28 wheel，回退 sdist 必被 gcc 4.8.5 拒绝 | `pyproject.toml` 的 `[tool.uv] constraint-dependencies` 约束四包 + 重新 `uv lock`（4.0 上可放宽） |
| PyPI 镜像 TLS | 直接可用 | `mirrors.cloud.aliyuncs.com` 证书签发给 `mirrors-ssl.aliyuncs.com`（名称不匹配） | pip 用 `--trusted-host`，uv 用 `--allow-insecure-host` |
| 端口 80 外部可达性 | 未明确 | 阿里云安全组已放行 80（公网 `curl http://<IP>/api/v1/health` 返回 200） | 是否收紧由业主决定 |
