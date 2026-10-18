# env-prep —— 验证环境准备脚本（《公网云服务器验证环境准备》落地工具）

把验证文档的第二、三、四章与验收项做成可重复执行的脚本。**目标机器：中科方德验证服务器
（x86_64，yum 系）**；NFC Server 4.0 到位前，可先在现有 NFS Server 3.1 机器演练（见文末）。

## 执行顺序（root）

| 步骤 | 脚本 | 对应文档章节 | 说明 |
|---|---|---|---|
| 1 | `install_python311.sh` | 第二章（Python 3.11.x + venv） | 源码编译，不动系统 python；OpenSSL <1.1.1 时自动先编译 1.1.1w 到独立前缀 |
| 2 | `init_ai_tools_env.sh` | 第三、四章（目录/账户/端口） | `/opt/agent/vision-agent/tools/` 六目录 + 六账户（无 sudo、锁密码、仅公钥）+ firewalld 18011-18016 |
| 3 | `collect_baseline.sh` | 第二章（基线记录）+ 验收项 7 | 生成 `baseline_<主机>_<日期>.md`，含差异项登记表模板 |
| 4 | `verify_acceptance.sh` | 验收项 1-7 | 逐项自检，全 PASS 退出码 0；`PORT_MODE=listen` 用于工具部署后的联调复检 |

## 各脚本要点

### install_python311.sh
- 默认 `PY_VERSION=3.11.14`；下载源依次尝试 npmmirror → huaweicloud → python.org，
  离线机器可 `PY_TARBALL=/path/Python-3.11.14.tar.xz bash install_python311.sh`。
- **3.1 演练机注意**：CentOS7 同代系统 OpenSSL 为 1.0.2，Python 3.11 的 ssl 模块要求 ≥1.1.1，
  脚本会自动先编译 OpenSSL 1.1.1w 到 `/usr/local/openssl-1.1.1`（独立前缀，不覆盖系统库，
  不违反基线冻结对全局组件的约束）；4.0 机器若系统 OpenSSL ≥1.1.1 则自动跳过。
- 验证段即验收项 2 的实测（版本/pip/venv/关键标准库 import）。

### init_ai_tools_env.sh
- 账户 = 工具名（xray/fracture/metallography/borescope/hotsection/fluorescence），
  密码锁定（仅 SSH 公钥），显式移出 wheel 组；目录 750，跨单位不可互访。
- 云安全组不会被脚本改动，完成后按输出提醒手工配置（18011-18016 限联调来源，80 按需）。
- `TOOLS`/`ROOT_DIR`/`PLATFORM_PORT` 可覆盖；工具增减由牵头单位统一变更后重跑（幂等）。

### collect_baseline.sh
- 覆盖文档要求的全部命令（uname/os-release/ldd/gcc/python3.11/pip/venv）+ 交付确认清单
  （CPU 型号与指令集、内存、磁盘与 /opt 空间、glibc/libstdc++、SSH 端口、防火墙/SELinux、容器套件现状）。
- 输出文件请归档并提交牵头单位，作为验收项 7 与后续比对的基线。

### verify_acceptance.sh
- 项 4 会实测账户无免密 sudo；项 5 会以每个工具账户真实创建再销毁 venv；
  项 6 两种模式：`free`（部署前端口应空闲）/ `listen`（部署后应监听且 `/health` 可应答）。
- 项 1 期望值 `EXPECT_OS="NFC Server 4.0"`，演练机上会如实 FAIL —— 这是差异记录，不是脚本错误。

## 在现有 NFS Server 3.1 机器演练（4.0 到位前）

1. 演练目标：跑通全部脚本、暴露 yum 依赖/下载源/OpenSSL 编译问题，沉淀操作记录；
2. 已知预期差异（记入 baseline 文件末尾差异表）：
   - 项 1 OS：实际 `NFS Server 3.1`，期望 `NFC Server 4.0`（4.0 后到，牵头单位已确认忽略）；
   - 内核 3.10（4.0 内核版本待实测，可能影响 Docker 上限与 getentropy 问题面）；
   - OpenSSL 1.0.2 → 触发脚本自建 1.1.1w 分支（正好把这条路径验证掉）；
3. 演练用 `EXPECT_OS="NFS Server 3.1"` 重跑 verify，除 OS 项外应全 PASS；
4. **4.0 机器到位后**：全部脚本原样重跑一遍（参数不变），重新采集基线并归档两份基线记录，
   差异项对照更新；云环境通过后，最终仍需按文档 §八 在正式生产环境完成迁移验证。

## 变更管理提醒

环境验收并开放使用后进入基线冻结（文档 §八）：以上脚本的重跑、参数变更（TOOLS/端口/账户）、
OpenSSL/Python 版本升级，均属环境变更 —— 需说明原因/影响/目标版本，经牵头单位确认后实施，
并同步重新采集基线记录。
