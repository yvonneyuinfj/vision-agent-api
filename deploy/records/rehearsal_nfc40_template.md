# NFC 4.0 VM 断网演练记录（模板）

> 用法：演练前复制本文件为 `rehearsal_nfc40_<YYYYMMDD>.md`，逐项执行并填写；
> 完成后按 §八 汇总，问题回写《园区网离线部署Runbook.md》与相关脚本。

- 演练日期：____  执行人：____  虚拟机：____（hypervisor / 分配 CPU-内存-磁盘）
- 依据：`deploy/园区网离线部署Runbook.md` §七（全程断网演练，出发前强制项）

## 阶段 A：联网准备（产出核对）

| # | 项 | 命令/动作 | 结果（PASS/FAIL/备注） |
|---|---|---|---|
| A1 | 快照 | hypervisor 打快照（断网执行前可回退） | |
| A2 | 基线归档 | `bash deploy/env-prep/collect_baseline.sh` | 基线文件：____ |
| A3 | OS 验收 | `EXPECT_OS="NFC Server 4.0" bash deploy/env-prep/verify_acceptance.sh` | |
| A4 | 关键版本登记 | glibc ____ / systemd ____（≥229？）/ OpenSSL ____（≥1.1.1？）/ docker ____ | |
| A5 | Python 3.11 | `install_python311.sh`（联网模式；OpenSSL ≥1.1.1 应显示"跳过自建"） | |
| A6 | 在线 C 分支安装 | `bash deploy/install_server.sh /root/QualityCheckingAgent` | venv 自检输出：____ |
| A7 | 制品包 | `make_offline_bundle.sh --on-verifier --with-image --with-mysql` + 开发机产物汇入 | bundle 大小：____ |
| A8 | SHA256SUMS | bundle 目录生成并抽检 | |

## 阶段 B：断网执行

### B0 断网确认（必贴输出）

```
$ ip route
（应无 default 路由）
$ curl -m 3 https://www.baidu.com
（应失败）
```

- [ ] 全部网卡已移除/禁用后开机

### B1–B10 Runbook §二 逐步执行

| Step | 项 | 结果 | 记录/问题 |
|---|---|---|---|
| 1 | 基线核对（断网下仅本机项 + 所内服务探活标记"演练豁免"） | | |
| 2 | `install_python311.sh` PY_TARBALL/OPENSSL_TARBALL 离线路径（或已装跳过） | | |
| 2 | `init_ai_tools_env.sh` | | |
| 3 | `OFFLINE_VENV_TGZ=… bash deploy/install_server.sh …` 离线 A 分支 | | venv 自检模块清单通过情况：____ |
| 3b | （如 A 崩）`OFFLINE_WHEELS_DIR=…` 离线 B 分支 | | 触发与否：____ |
| 4 | `.env`（env.园区网.example 底稿；RAGFlow/网关留空、Ceph→本机 minio 替身或留空） | | |
| 5 | 本机 MySQL：`docker load -i mysql_8.0.tar` + run + init.sql + create_platform_user.sql | | |
| 6 | 起平台：health `code=0, database=ok`；**绑 80 走 AmbientCapabilities（无 setcap、NNP 保留）** | | journalctl 有无 Errno 13：____ |
| 7 | `sync_capabilities --no-tasks` + switch_tool_endpoint 逐个切换 | | |
| 8 | 金相**裸装**（容器化指南 §10）+ `ai-tool@metallography`；容器 load 路线复验一次 | | 指纹 + 实打结果：____ |
| 8 | 孔探/热端/荧光 离线指南全流程 | | 各自指纹 + 实打：____ |
| 9 | 前端 E2E（六场景；RAGFlow/LLM 降级项按替身验收） | | |
| 9 | 外网残留检查 `journalctl -u quality-agent \| grep -iE 'timeout\|dashscope\|aliyuncs'` | | 命中行：____ |
| 10 | 备份 `MYSQL_CONTAINER=qc-mysql bash deploy/backup_platform_data.sh` + 恢复演练 | | |

### B11 降级路径专项（园区网实况预演）

| 场景 | 操作 | 期望 | 实测 |
|---|---|---|---|
| RAGFlow 不可达 | `.env` 留空启动 | 知识层降级、主链路不阻断、无刷屏 ERROR | |
| 模型网关缺位 | LLM 四行留空 | health `vlm_available:false`、报告退纯模板不报错 | |
| 对象存储降级 | MINIO 段留空 | 产物本地落盘 `data/`、上传/报告链路正常 | |
| Ceph 替身 | minio 容器当 S3 后端 | 建桶/上传/预签名 URL 全通（minio SDK 兼容性代理验证） | |

## 汇总结尾

- 遗漏联网点清单（断网下任何解析/超时报错）：____
- 4.0 新分支验证结论（AmbientCapabilities / 金相裸装 / OpenSSL 跳过自建 / 约束四包 import）：____
- 制品包缺漏项（现场才发现"要下载"的东西）：____
- 需回写 Runbook / 脚本的修改：____
- 结论：□ 通过，可进园区网　□ 有条件通过（条件：____）　□ 不通过，修复后重演
