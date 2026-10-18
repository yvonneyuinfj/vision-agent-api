## 启动

| 端口 | 实例 | 能力表 | 验证 |
|---|---|---|---|
| **18011** | **X光/DR 真工具**（`local-tools/xray`，DR Structure OPI Sequence API） | `dr_defect_detection_v1` | state041/042 OPI 双帧推理：code:0，bi_chi/jing_zi 双模型产出结构检测+间隙测量 |
| 18012 | mock·fracture | v10 | healthy |
| **18013** | **金相/断口真工具**（`local-tools/metallography`，显微组织识别与定量分析 API） | `metallography_defect_detection_v1` | 显微图推理：code:0，UNet 分割+GAMMA定量（γ′占比 0.517、36颗粒） |
| **18014** | **孔探真工具**（`local-tools/borescope`，孔探叶片缺陷识别 API） | `borescope_defect_detection_v1` | compressor/turbine ONNX 推理：code:0、/health 两模型 exists |
| **18015** | **热端部件真工具**（`local-tools/hotsection`，热端超分+缺陷检测 API） | `infrared_defect_detection_v1` | test_abnormal 推理：code:0，检出第3齿 shape_anomaly，超分+检测 |
| **18016** | **荧光真工具**（8003→18016 迁移完成） | `fpi_defect_detection_v1` **v16** | test01.jpg 基准推理：200 / 7 事件 / 6 真缺陷 / unqualified，**产物 URI 基址已是 18016** |

> 2026-09-21 起：`20260921` 目录新增 3 个真实交付（X光/金相断口/热端部件）已按荧光先例在 macOS 本地搭建为真工具（孔探早已有 18014）。18011/18013/18014/18015 已由各自真工具实例把原 mock·dr/metallography/borescope/infrared 同端口替换（注意 mock 若也在跑会端口冲突，真工具优先）。18012 mock·fracture 暂保留（断口未在本次批次交付）。

## 真实工具启动（`scripts/dev/` 下 start_<tool>_tool.sh）

```bash
# 逐个启动真工具（幂等：端口在跑自动跳过；等模型加载完才返回）
bash scripts/dev/start_xray_tool.sh           # X光/DR  → 18011
bash scripts/dev/start_metallography_tool.sh  # 金相/断口 → 18013
bash scripts/dev/start_borescope_tool.sh      # 孔探    → 18014
bash scripts/dev/start_hotsection_tool.sh     # 热端部件 → 18015

# 荧光真工具（18016）
bash scripts/dev/start_fluorescence_tool.sh

# mock 仍可用（18011-18015 一场景一实例；按**端口占用**跳过——5 场景已交付真工具，
# 全在跑时本脚本实际只补未交付的 fracture:18012；fpi 已是真工具，不入 mock 实例表）
bash scripts/dev/start_mock_tools.sh

# 停止（缺省指定名称；荧光/孔探等都支持）
bash scripts/dev/stop_local_tools.sh
```

- 各工具目录：`local-tools/{xray,metallography,borescope,hotsection,fluorescence}`，各自独立 `.venv`（macOS 兼容轮子，端口号写死在部署配置/启动脚本）；
- 日志：`logs/dev/{xray,metallography,borescope,hotsection,fluorescence}.out`；PID：`logs/dev/*.pid`；
- 实例是 **nohup 独立进程**——会话结束也持续运行，随时可用上方命令启停；
- 每实例身份/端口映射与 `deploy/ai-tools/README.md` 拓扑表一致；
- 版本适配：这几个交付的离线锁均面向 Linux x86_64（`torch==2.14+cpu`、`2.1.2+cpu` 等 macOS 无同标签轮子），本地 venv 使用 macOS 可装的对应版本（X光/热端 torch 2.1.2；金相 torch 2.6.0），已实测模型加载+推理通过。服务器侧(x86_64)仍按交付离线包原锁部署。

注意：平台(:8010) 仍是我会话里的后台任务（bash-6），会话结束会停；届时你可用 `bash scripts/dev/` 起工具层后，再按平常方式起平台（`.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8010`）。服务器侧操作（Op-B/C0/C）继续暂缓，等你安排。

## 一键启动 / 停止（全部场景工具 + 平台）

```bash
# 一键启动 5 个真实检测场景工具（X光18011/金相18013/孔探18014/热端18015/荧光18016）+ 平台(8010)
bash scripts/dev/start_all_scene_tools.sh
# 追加 mock（仅 fracture 断口这一未交付槽位 → 18012；与真实工具同端口占用自动跳过）
bash scripts/dev/start_all_scene_tools.sh --with-mock
# 只起工具层、不起平台
bash scripts/dev/start_all_scene_tools.sh --no-platform

# 一键停止（5 真实工具 + 平台；--keep-platform 保留平台；位置参数可指定工具名）
bash scripts/dev/stop_all_scene_tools.sh
```

- `start_all_scene_tools.sh` 默认只起 5 个**真实**交付工具（不含 mock），逐调用上面各 `start_<tool>_tool.sh`（各自幂等）后启动平台，尾部探活 18011/18013/18014/18015/18016/8010。
- 平台日志 `logs/dev/platform8010.out`，PID `logs/dev/platform8010.pid`。
- 两脚本均已 `bash -n` 校验通过；`start_all_scene_tools.sh` 已在「5 工具 + 平台全在跑」下实测幂等通过（全部 [skip]/探活 200）。

## 部署布局：`local-tools`（本地）vs `/opt/agent/vision-agent/tools`（阿里云服务器）

**本目录（`scripts/dev/`）的启动脚本只面向「本地 development 布局」**，工具代码在
`QualityCheckingAgent/local-tools/<tool>`，用 `local-tools/<tool>/.venv` 直接 nohup 启动。
这是**联调/演示用的临时形态，不等于生产部署**。

**阿里云服务器（生产/验证机）的工具部署目录是 `/opt/agent/vision-agent/tools/<tool>`，不是本项目目录**：
- 拓扑与端口映射见 `deploy/ai-tools/README.md`（xray:18011 / fracture:18012 /
  metallography:18013 / borescope:18014 / hotsection:18015 / fluorescence:18016）；
- 部署方式：**非容器化裸机**，每工具独立账户 + 独立 venv，systemd 单元 `ai-tool@<tool>`
  （脚本 `deploy/install_ai_tool.sh` 与模板 `deploy/systemd/ai-tool@.service`，包契约见
  `deploy/ai-tools/README.md` §2）；
- 因此**不要把 local-tools 当作服务器工具目录**。服务器上工具代码落在 `/opt/agent/vision-agent/tools/<tool>/app/`，
  由 install_ai_tool.sh 安装，用 `systemctl <start|status> ai-tool@<tool>` / `journalctl -u ai-tool@<tool>` 管理；
- 平台在服务器上为 `/opt/agent/vision-agent/quality-agent`（systemd `quality-agent.service`，端口 80），整体 Runbook 见 `deploy/README.md`。

> 一句话：**本地联调 `local-tools` + `scripts/dev/start_*`；生产/服务器 `/opt/agent/vision-agent/tools` + `deploy/` + systemd。**

## 园区网离线制品包（`make_offline_bundle.sh`）

向所内园区网（纯内网）正式部署前的制品准备，两段式：

```bash
# 开发机（有网）：前端 dist + source.tar.gz + 锁定清单/wheels + Python/OpenSSL tarball
bash scripts/dev/make_offline_bundle.sh
# 验证机（与目标机 glibc 同代）：整 venv 迁移包（主路径）+ 金相容器镜像 tar
bash scripts/dev/make_offline_bundle.sh --on-verifier --with-image
```

产物汇入 `offline-bundle/`（附 SHA256SUMS）；现场执行与风险预案见
`deploy/园区网离线部署Runbook.md`，配置模板 `deploy/env.园区网.example`，
安装器离线分支（`OFFLINE_VENV_TGZ`/`OFFLINE_WHEELS_DIR`）见 `deploy/install_server.sh`。

植入 dist 后可用冒烟脚本验证后端同源托管（无需 MySQL，TestClient 不跑 lifespan）：
```bash
.venv/bin/python scripts/dev/smoke_frontend_hosting.py   # / 直出 index.html、/login 深链不 404、静态资源/缺失资源行为
```

## 真实工具 4 场景 E2E（孔探/热端/X光/金相）

能力注册已在 MySQL `quality_inspection.capability_registry` 落到各自真端口（`scripts/dev/run_real_tools_e2e.py` 使用同一注册表，与平台一致）：

| 场景 | 真工具端口 | capability | 语义归一 |
|---|---|---|---|
| borescope（孔探） | 18014 | `borescope_defect_detection_v1` | 缺陷（result.defects） |
| infrared（热端） | 18015 | `infrared_defect_detection_v1`（需 X-API-Key 头） | 缺陷（result.defects） |
| dr（X光/DR） | 18011 | `dr_defect_detection_v1`（`image_field=images`，多 OPI 一次调用） | 分析上报（normal，结构/间隙） |
| metallography（金相断口） | 18013 | `metallography_defect_detection_v1` | 分析上报（normal，γ相定量） |

完整 agentic E2E（不经 HTTP/auth，直连 AppContainer 同图）：
```bash
# 需先把 4 场景输入 stage 到 data/inputs/_upload/（e2e_*.jpg/png + 2 个 .opi），
# 随后整脚本跑 4 个真实质检任务（工具推理 + 归一 + 判级 + 复核门自动确认 + 报告）
.venv/bin/python -m scripts.dev.run_real_tools_e2e
```

配套改动（2026-09-22 接入）：
- `app/capabilities/adapters/tool_profiles.py`：已含 borescope/metallography/dr_structure 与热端（顶层 result 规范形态透传）归一；孔探/热端→缺陷、X光/金相→分析，不伪造成标量缺陷。
- `app/repositories/file_repo.py`：`_SAFE_EXTS` 增 `.opi`（X光 OPI 序列数据输入）。
- X光/DR 走 `images` 字段、多 OPI 文件一次调用（DR capability `input_required.image_field=images`、`images=2`）。

E2E 判据（每任务）：`status=completed`、`capability_used`=该场景真工具、标准化 result 体现正确语义（孔探/热端 has defects；X光/金相 normal 分析 + 报告 analysis_lead 明确）、`report_json` 非空。