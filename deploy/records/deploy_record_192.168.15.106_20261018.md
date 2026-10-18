# 部署记录 · 192.168.15.106（方德高可信 4.0）

- **记录日期**：2026-10-17
- **执行人**：于小耘
- **对象**：quality-agent 平台 + 本机 MySQL 裸装 + 孔探工具（六工具之一）
- **口令/凭据**：一律不落本文档，见《环境信息收集表》登记项
- **配套文档**：《15.106协调事项清单.md》《15.106混部主机部署安全隐患通报.md》

---

## 一、机器档案（进场体检结论）

| 项目 | 实测值 | 对部署的影响 |
|---|---|---|
| 发行版 | 中科方德 高可信服务器 OS 4.0 RTM4-G340，x86_64 | 与通用版 NFC 4.0 同代，制品包通用 |
| kernel / glibc | 4.19.113-16.nfs4 / **2.28** | ≥2.17 底线，manylinux2014 wheels 全兼容 |
| systemd | 239 | 无 219 时代的模板单元 bug |
| OpenSSL | 1.1.1c（so.1.1） | Python `_ssl` 直链系统库，免自编译 |
| SELinux | **Disabled** | 高可信版三员强制场景不存在（仍列备案项请所内确认） |
| rsync | 3.1.3 原生自带 | install_server.sh 免补装 |
| libaio / ncurses5 / tinfo5 / bz2 | 全在 | MySQL 裸装零障碍、免兼容软链 |
| 系统 Python | 仅 3.6.8 | 需源码编译 3.11.14 |
| 编译头文件缺项 | readline-devel、ncurses-devel | pydeps 补装（唯一缺包清单） |
| 3306 | 原生 mysqld 在跑（且 33060 全网卡开放） | 我方数据库改用 **18020** 独立实例 |
| 18000–18020 段 | 进场时干净（后发现 0.0.0.0:18201 无主服务，紧邻段） | 登记至安全通报 |
| 资源 | 128 核 / 125 GiB / 2 TiB（根分区 251 GiB，用 5%） | 充裕 |
| 中文字体 | Noto CJK 自带 | 报告渲染免装字体 |
| 网络 | 无出口（yum 源不可达，实测确认） | 一切依赖走介质 |

**混部现状**：本机为多项目混部机，已存在 hkanswer 应用（Next.js :1015 全网卡 + PostgreSQL17 三库）、MinerU 文档解析簇（127.0.0.1:40020 + 40000–40010）、原生 MySQL、Samba/NFS/CUPS/Exim/cockpit、libvirtd、wpa_supplicant 等；firewalld 未运行。详见《安全隐患通报》。

---

## 二、部署时间线

### 2.1 Python 3.11.14 编译（约 20 分钟）

- 制品：`/opt/offline_bundles/Python-3.11.14.tar.xz` + `pydeps/`（readline-devel、ncurses-devel RPM）。
- `./configure --prefix=/usr/local`：`_ssl/_hashlib/_sqlite3/zlib/bz2/_lzma/readline` 全 yes；missing 仅 `_gdbm/nis/_tkinter/uuid`（均判可容忍，uuid 走纯 Python 回退实测 OK）。
- `make -j64` → **`make altinstall`**（绝不 make install，保护系统 python3=3.6.8）。
- 终验四连全绿：`3.11.14` / `import ssl,sqlite3,ctypes,zlib,bz2,lzma,readline → ALL MODULES OK` / uuid 回退 OK / 系统 3.6.8 完好。
- 注意：readline.h 位于 `/usr/include/readline/` 子目录，直查文件名的体检脚本会假报 MISS（两代机器同款假警）。

### 2.2 env-prep 账户体系（一次通过）

- `bash quality-agent/deploy/env-prep/init_ai_tools_env.sh`：六工具账户+目录建成（xray 18011 / fracture 18012 / metallography 18013 / borescope 18014 / hotsection 18015 / fluorescence 18016）。
- 脚本 WARN：firewalld 未运行 → 暴露面控制依赖园区网边界 + 我方回环绑定纪律（已登记）。
- qcagent 平台账户此阶段不存在（由 install_server.sh 创建），符合预期。

### 2.3 MySQL 8.0.46 裸装（含一次事故，见 §三）

- tarball：`mysql-8.0.46-linux-glibc2.17-x86_64-minimal.tar.xz` → `/usr/local/mysql`（软链）。
- `--initialize-insecure`，数据目录 `/opt/qc-mysql-data`，socket `/opt/qc-mysql-run/mysqld.sock`。
- 单元 **`qc-mysqld.service`**（独名，原因见事故记录），`--bind-address=127.0.0.1 --port=18020`。
- 建库建号：`scripts/sql/init.sql` + `deploy/sql/create_platform_user.sql`（三处 `'qc_agent'@'%'` 已全部 sed 收敛为 `@'localhost'`，防 1410）。
- 终验：`qc_agent` 登录 `quality_inspection`，7 表就位（agent_sessions / capability_registry / file_objects / knowledge_docs / session_messages / tasks）。
- root 口令已设并存 `/opt/qc-mysql-root.pass`（600）。

### 2.4 平台安装与点火（install_server.sh 6/6 一次通过）

- 制品：`source.tar.gz`（28MB）+ `qcagent-venv.tar.gz`（78MB，venv 迁移主路径）。
- 命令：`APP_PORT=18000 OFFLINE_VENV_TGZ=... bash deploy/install_server.sh /opt/offline_bundles/quality-agent`（**不传 APP_DIR**，默认规范路径 `/opt/agent/vision-agent/quality-agent`）。
- 3/6 venv 自检通过（解释器即本机 2.1 节编译的 3.11.14）。
- **shebang 软链**（制品包已知欠账）：`ln -s /opt/agent/vision-agent/quality-agent/.venv /opt/agent/vision-agent/.venv`，uvicorn 0.52.3 可执行验证通过。**每次重跑 install_server.sh 后需复查此软链**（rsync --delete 可能清掉）。
- .env：四处占位符处理——DATABASE_URL（18020 + 平台账号）、RAGFLOW_API_KEY 置空、MINIO 双键置空（降级本地存储）；LLM 网关 `http://192.168.198.10:8000/v1` **跨网段实测可达**（/v1/models 返回 Qwen3.5-122B-A10B），照抄可用；AUTH_* 已配置（值见收集表）。
- `systemctl start quality-agent` → active，`/api/v1/health` 正常返回；自启 enabled。
- 能力注册表 `sync_capabilities --no-tasks` 已执行：6 行就位（borescope/fracture/metallography/infrared/dr/fpi），除 fpi 预指 18016 外均指 mock `127.0.0.1:8002`。

### 2.5 孔探工具（六工具 1/6 上线）

- 包：`/opt/offline_bundles/opt/ai-tools/borescope/app/Borescope_USB_Package`（10-05 出厂版，与 8.18 同批）。
- 流程（旧机经验全部前置执行，一次通过零返工）：
  1. `cp -a` 平铺至 `/opt/agent/vision-agent/tools/borescope/`；
  2. **`rm -rf venv`**（厂商包自带 venv 有路径烙印，必须删掉重建）+ `chmod +x scripts/*.sh`；
  3. `sed` 一次改 `config/service.json`：端口 18104→**18014** + host 0.0.0.0→**127.0.0.1**（旧机是首启后发现违规返工，本次首启前完成）；
  4. `install_offline.sh`：numpy 1.24.4 / cv2 4.10.0 / onnxruntime 1.16.3 三 `[通过]`，双模型 compressor/turbine best.onnx 各 12M 校验成功；
  5. `start.sh` → **首启即回环监听 127.0.0.1:18014**，health `healthy` 带厂商指纹（model/sha256），chown 归还 borescope。
- **未完成**：注册表 endpoint mock→18014 切换及端到端传图验证（切换脚本已备好，见 §四）。

---

## 三、事故记录（本次新坑，已固化）

### 3.1 systemd 单元撞名：裸装 MySQL 静默不启动

- **现象**：单元写入 `/etc/systemd/system/mysqld.service` 后 `enable --now`，`is-active` 报 active，但 `ss` 里 18020 无监听。
- **根因**：本机原生 MySQL 的单元就叫 `mysqld.service`（开机已拉起 /usr/libexec/mysqld，PID 12732）。systemd 见同名服务已 active 便不再启动新实例；`is-active` 张冠李戴报的是原生进程。journal 关键证据：`Current command vanished from the unit file`；`status` 的 Main PID/CGroup 指向别人二进制。
- **处置**：单元改名 **`qc-mysqld.service`** → daemon-reload → `enable --now qc-mysqld` → `systemctl enable mysqld` 恢复原生单元自启符号链接。修复后两实例并存：原生 3306（不碰）+ 裸装 18020（仅回环）。
- **教训固化**：部署手册阶段三已更新（单元独名 + 端口 18020 + 双 mysqld 并存验证判据）；通用坑已入记忆库。

### 3.2 终端粘贴吃字（环境性顽疾，非事故但全程设防）

- 本终端长命令粘贴高频吞字符：`-l` 粘成 `-1`（psql）、路径漏层级、`ln` 成 `ls` 等。
- 对策已贯穿执行：能软链不 sed、heredoc 整段贴、高危命令回车前肉眼核对、复杂操作改脚本。

---

## 四、当前状态快照（10-18 离场时点）

| 组件 | 状态 | 备注 |
|---|---|---|
| quality-agent.service | ✅ active + enabled | :18000，health 正常，RAGFlow/MinIO 按设计降级 |
| qc-mysqld.service | ✅ active + enabled | :18020 仅回环，7 表，qc_agent@localhost |
| 孔探（borescope） | ✅ 运行中 | :18014 仅回环，health healthy；**注册表未切换、E2E 未做** |
| 其余五工具 | ⏸ 待介质 | 账户/目录/端口已就位 |
| 切换脚本 | 已部署 | `/usr/local/bin/qc-switch-endpoint.sh`（root 700）——裸装版四步：UPDATE→回读→热加载→探活 |
| 浏览器侧验收 | ⏸ 未做 | 登录页可达性、背景图双-assets 问题是否存在（同批 dist 大概率存在，修法：`cd frontend/dist/assets && ln -s . assets`） |

**孔探接线一行（回所内后执行）**：

```bash
qc-switch-endpoint.sh borescope_defect_detection_v1 http://127.0.0.1:18014
```

---

## 五、待办与欠账清单

**现场待办（回 15.106 后按序）**：
1. 孔探注册表切换（上面那行）+ 浏览器端到端传图 + `tail tools/borescope/logs/api_server.log` 实锤；
2. 办公机访问 `http://192.168.15.106:18000/` 验收登录页；背景图 404 若复现 → 自环软链两分钟修完；
3. 介质到位后装荧光（45 包 wheels 过盘审批中）→ `qc-switch-endpoint.sh fpi_defect_detection_v1 http://127.0.0.1:18016`。

**外部依赖（见协调清单）**：RAGFlow 地址+写权限 key（0.24.1 兼容已确认）、Ceph RGW 四件套、VLM 模型 id、热端/金相/断口/X光四工具包过盘。

**工程欠账（不阻塞，择期）**：
- `qcagent-venv.tar.gz` 制品包在规范路径重打（消灭 shebang 软链依赖）；
- 前端 dist 重构建（CSS 绝对路径根治双-assets）随下批制品进网；
- 平台口令升 12 位+（两代机器同款债，验收前统一换）；
- 切换脚本仓库侧出裸装适配正式版（口令改读 /opt/qc-mysql-root.pass）。
