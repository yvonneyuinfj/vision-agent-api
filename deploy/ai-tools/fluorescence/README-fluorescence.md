# 荧光真实工具（blade_defect_detector）服务器入驻方案 —— Op-C

交付件：`thread-defect-api-20260910`（荧光渗透，YOLOX-s CPU，FastAPI/uvicorn）。
本地已全链路验证（2026-09-15，Mac/Python 3.11.14）：/health、test01.jpg 基准
（7 事件/6 真缺陷/unqualified，与交付方参考一致）、自有荧光图 ×2、异常用例 ×4、
平台 E2E（提交→推理→归一→判级→复核→报告→四产物落盘）全部通过。

## 1. 上传物（server-bundle/，本机 local-tools/fluorescence/server-bundle/ 组装）

| 文件 | 体积 | 说明 |
|---|---|---|
| fluorescence-pkg.tar.gz | ~73MB | 安装包：start.sh + tool.env + requirements-server-glibc217.txt + thread-defect-api-20260910/（已剔除本地运行产物 results/runtime/logs/__pycache__；权重 best_ckpt.pth sha256=4F2CA9AB…C631C2 与验收清单一致） |
| fluorescence-wheels.tar | ~265MB | 45 个离线 wheel（交付方 53 个裁 9 补 1，见 §3 偏差） |
| test01.jpg | ~1.6MB | 交付方基准验证图（预期 7 事件/6 真缺陷/unqualified） |
| SHA256SUMS | - | 上述全部文件摘要（上传后服务器端 `sha256sum -c` 核验） |

上传方式：`ssh 'cat > /root/<file>'` 管道 + 两端 md5/sha256 核对（既有可靠模式）；
wheels.tar 较大，视带宽可拆两段（`split -b 150m`）上传后 `cat` 拼回并核 sha。

## 2. 服务器安装步骤（root；已含在 Op-C 审批范围）

```bash
# 0) 前置：平台代码已同步 A 阶段改动并重启（见 §4）；荧光账户/目录已就绪（env-prep 完成）
mkdir -p /root/fluorescence-pkg /root/fluorescence-wheels
tar -C /root/fluorescence-pkg -xzf /root/fluorescence-pkg.tar.gz
tar -C /root/fluorescence-wheels -xf /root/fluorescence-wheels.tar
cd /root && sha256sum -c SHA256SUMS   # 对上传物核验

# 1) 字体核查（只读）：交付方 report_service 候选含 wqy/Noto CJK
fc-list :lang=zh | head -3
#    为空则二选一：yum install -y wqy-microhei-fonts（推荐，标准源）
#    或上传开源字体至 /opt/agent/vision-agent/tools/fluorescence/fonts/ 并在 tool.env 设 REPORT_FONT_PATH

# 2) 安装（离线 wheels，安装器自带 health 轮询，模型加载约 15-60s）
bash /root/QualityCheckingAgent/deploy/install_ai_tool.sh fluorescence /root/fluorescence-pkg \
  --tool-name blade_defect_detector \
  --requirements requirements-server-glibc217.txt \
  --offline /root/fluorescence-wheels

# 2b) 补装 pycocotools（--no-deps，必做；见 requirements-server-glibc217-nodeps.txt）
#     漏掉这一步的后果：服务能起、/health 返回 status=degraded + model.loaded=false，
#     推理返回 code 2001 "YOLO model is not loaded"——不报错到进程外，极易被误判为"已入驻"。
/opt/uv-tool/bin/uv pip install --python /opt/agent/vision-agent/tools/fluorescence/.venv/bin/python \
  --no-index --find-links /root/fluorescence-wheels/wheels --no-deps \
  -r /root/fluorescence-pkg/requirements-server-glibc217-nodeps.txt
systemctl restart ai-tool-fluorescence    # 单元名见 §6

# 3) 单机自检（规范附录 C）：
curl -s http://127.0.0.1:18016/health            # code:0 + model.loaded:true + 权重哈希
#    基准推理（预期 7 事件/6 真缺陷/unqualified）：
curl -s -X POST http://127.0.0.1:18016/api/v1/inference \
  -F "image=@/root/test01.jpg;type=image/jpeg" -F "task_id=T_ACCEPT_001" \
  -F 'parameters={"part_id":"PART_ACCEPT","device_id":"QC-SERVER"}' | head -c 400
#    异常用例：缺 task_id→422/1001；坏图→400/1003；错 Content-Type→415/1002
#    torch glibc 冒烟（平标签 wheel 必验）：
/opt/agent/vision-agent/tools/fluorescence/.venv/bin/python -c "import torch,cv2,yolox,PIL; print(torch.__version__, cv2.__version__, yolox.__version__, PIL.__version__)"

# 4) 端点切换（平台侧；meta_json 全量替换——真实工具参数契约 extra=forbid）
bash /root/QualityCheckingAgent/deploy/switch_tool_endpoint.sh \
  fpi_defect_detection_v1 http://127.0.0.1:18016 http://127.0.0.1:18016/health \
  '{"families":["defect"],"default_params":{"part_id":"UNKNOWN","device_id":"QC-PLATFORM"},"input_required":{"images":1,"image_field":"image"},"max_images":1,"note":"真实工具 blade_defect_detector（荧光渗透 thread-defect-api-20260910，/opt/agent/vision-agent/tools/fluorescence :18016）；parameters 仅 part_id/device_id/timestamp；响应归一 tool_profiles.py"}'

# 5) E2E：HTTP API 提交真实荧光图任务（yingguang_yolox_test.jpg）→ completed/awaiting_review
#    → 复核 confirm → 报告含四产物引用；GET /api/v1/capabilities/health fpi 绿
```

## 3. 偏差项（相对交付方基线，手册 §八 要求；同步登记 deploy/records/）

| # | 事项 | 交付基线 | 服务器实际 | 理由/验证 |
|---|---|---|---|---|
| 1 | Python | 3.11.16 | 3.11.14 | 同 minor；基线冻结不另装；本地 3.11.14 全链路已验证 |
| 2 | pillow | 12.3.0（manylinux_2_27/2_28） | 10.4.0（manylinux2014，PyPI sha256=5e84b6cc…e7d6） | glibc 2.17 装不上 12.x；PIL 在运行时导入链（inference→data→PIL）必须保留；10.4.0 为平台 mock 已验证版本，API 兼容（Image.open 基础用法） |
| 3 | matplotlib 栈 | matplotlib+contourpy+cycler+fonttools+kiwisolver+pyparsing+python-dateutil+six | 全部裁剪（8 包） | contourpy 1.3.3 仅 manylinux_2_27/2_28；且 matplotlib 不在服务端运行时导入链（仅离线 sweep.py→metrics.py 阈值扫描工具用）——静态导入链已核 |
| 4 | YOLOX 安装 | pip editable + 预编译 FastCOCOEvalOp | PYTHONPATH 源码直挂（start.sh），跳过 setup.py | 服务器 gcc 4.8.5 无法编译 torch 2.1.2 C++17 扩展；该算子仅 COCO 评估用（运行时 try/except 兜底）；本地同方案推理/产物全通过 |
| 5 | 安装器 | 交付方 install_offline.sh（root 路径 /opt/industrial-api、pip、/opt/venvs） | 平台 install_ai_tool.sh（/opt/agent/vision-agent/tools/fluorescence、uv、.venv、独立账户、systemd 模板单元） | 统一入驻管线与安全模型（账户无 sudo、目录 750、回环绑定）；步骤语义与交付方脚本一致（校验→解包→venv→离线装→冒烟） |
| 6 | 端口/绑定 | 8000（start_server_nfscns.sh 默认） | 18016 @ 127.0.0.1 | 验证环境拓扑分配表；公网不可达（决策 #2） |
| 7 | 响应外壳 | 规范 §7 result 外壳 | 工具 data 外壳 → 平台 tool_profiles.py 归一 | 不改交付件；作为规范偏差项反馈交付方（9/18-19 联调） |
| 8 | 错误码语义 | 规范：1003=超大/2001=参数/4001=内部 | 工具：1003=图不可读/1004=超大/2001=模型未加载/4001=认证失败 | 平台按 HTTP 状态映射（413/415/422/503/504 与工具语义一致），错误分类不受影响；偏差项反馈交付方 |
| 9 | API_KEY | 远端部署启用 X-API-Key | 服务器实例不启用（回环绑定，平台同源调用） | tool.env 未设 API_KEY；如后续要求启用，需同步平台侧 header 注入（现无此机制，另行开发） |
| 10 | pycocotools 与 matplotlib | 交付方基线两者都在 | wheel 包保留 pycocotools 但裁掉 matplotlib → 主清单在 `--no-index` 下无解 | 2026-09-29 实跑修正：裁 matplotlib 时对，但 **pycocotools 不能随之从主清单剔除**（`yolox/data/datasets/coco.py` 顶层导入，服务器实测 ModuleNotFoundError→模型不加载）。方案：pycocotools 移到 `requirements-server-glibc217-nodeps.txt`，以 `--no-deps` 装（运行时仅需 numpy）；见 §2 步骤 2b |
| 11 | systemd 单元形态 | `ai-tool@<tool>`（模板单元，install_ai_tool.sh 起停用） | 本机实际用 **per-tool 单元** `ai-tool-<tool>.service` | 本机 systemd 219 下模板实例报 `status=203/EXEC`、日志里 ExecStart 路径的 `%i` 未展开（`/opt/agent/vision-agent/tools/%i/start.sh: No such file or directory`）。见 §6 的假绿陷阱 |

## 4. 前置依赖：平台代码同步（A 阶段改动，单独审批项）

服务器 `/opt/agent/vision-agent/quality-agent` 运行的平台代码需先同步 4 个文件（SRC→APP 同既有模式）：
- `app/capabilities/adapters/tool_profiles.py`（新增，响应归一 profile）
- `app/capabilities/adapters/http_adapter.py`（profile 钩子 + 图片 MIME 按扩展名派生）
- `app/agents/tools/infer_tool.py`（产物本地化支持绝对 URI + 文件名扩展名推导）
- `tests/capabilities/test_tool_profiles.py`（新增，10 用例）

同步后：`systemctl restart quality-agent` → health 200 → 服务器 pytest 全量（预期 530 passed）
→ fpi 切换才允许执行。回滚：还原 4 文件 + restart（平台行为对 mock 完全向后兼容，
profile 仅对 data 外壳且 tool_name=blade_defect_detector 的响应生效）。

## 5. 回滚

- 端点级：`switch_tool_endpoint.sh fpi_defect_detection_v1 http://127.0.0.1:8002`（若 mock 在位）
  或保持 18016 但 `systemctl stop ai-tool@fluorescence`（能力巡检转红，任务显式失败而非误判）；
- 实例级：`systemctl disable --now ai-tool@fluorescence` + 清 `/opt/agent/vision-agent/tools/fluorescence/{app,.venv,start.sh,tool.env}`；
- 平台级：§4 四文件还原 + restart。

## 6. 现网实录（2026-09-29 阿里云验证机首次真实入驻）

实例 `iZ2zeiuczdbdkgyfuir2j8Z`（NFS Server 3.1 / glibc 2.17 / systemd 219）。此前该机的六个
实例**全是 mock**（`/opt/agent/vision-agent/tools/*/app/main.py` = `mock_service/main.py`），本节的 Op-C 是荧光
第一次真正落到这台机器。

**假绿陷阱（重要）**：`install_ai_tool.sh` 第 7 步 `systemctl restart ai-tool@$TOOL` 后轮询
`/health` 判定成功。本机模板实例起不来（`203/EXEC`，`%i` 未展开），而端口仍被旧的
per-tool 单元 `ai-tool-fluorescence.service` 里的**旧 mock 进程**占着，于是 1 秒就 "health OK"
——载荷是 `tool_name=fpi_defect_detection` 的 mock。判别方法：真工具的 `/health` 是
`data` 外壳且带 `model.loaded`；mock 是扁平外壳且 `defect_domains` 恒为 6 项。
本机的正确动作：`systemctl restart ai-tool-fluorescence`（per-tool 单元名 = 目录名）。

实跑结果（逐项实测，非推断）：
- 权重加载后 `checkpoint_sha256=4F2CA9AB07761C54973F90844B02E6B72C1BB095F91EB25AE999E55422C631C2`，
  与包内 `MODEL_SHA256.txt` 一致；`model.device=cpu`，`status=healthy`。
- 基准图 `test01.jpg`：`model_event_count=7`、`true_defect_count=6`、
  `business_decision=unqualified`、`artifacts` 4 项，耗时 ~10.2s（与交付方参考一致）。
- 平台侧 E2E（AppContainer 同图，不经 HTTP/auth）：任务 `completed`，
  归一 6 条缺陷（BP/SP 带 bbox，置信度 0.26–0.83）、判级 `defect`、报告已产出，
  四产物（original/result-image/result-json/report）经平台回取全部 200。
- 其余五场景仍为 mock：18011–18015 载荷均为扁平 mock 外壳 + `defect_domains=6`，
  注册表端点未动；`systemctl is-enabled` 六实例 + quality-agent 全部 enabled（重启后自起）。
