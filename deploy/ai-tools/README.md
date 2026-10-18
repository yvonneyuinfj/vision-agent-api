# /opt/agent/vision-agent/tools 算法工具入驻手册（mock 实例与真实交付工具通用）

适用：公网验证服务器（NFS Server 3.1，x86_64，glibc 2.17，systemd 219，Python 3.11.14）。
配套资产：`deploy/systemd/ai-tool@.service`（模板单元）、`deploy/install_ai_tool.sh`（安装器）、
`deploy/switch_tool_endpoint.sh`（端点切换）、`deploy/env-prep/`（账户/目录/防火墙初始化）。

## 1. 拓扑与映射（决策 #1/#2：每工具独立实例，一律绑定 127.0.0.1）

| 实例 %i | 账户 | 目录 | 端口 | 平台 capability_id | scene | mock TOOL_NAME |
|---|---|---|---|---|---|---|
| xray | xray | /opt/agent/vision-agent/tools/xray | 18011 | dr_defect_detection_v1 | dr | dr_defect_detection |
| fracture | fracture | /opt/agent/vision-agent/tools/fracture | 18012 | fracture_defect_detection_v1 | fracture | fracture_defect_detection |
| metallography | metallography | /opt/agent/vision-agent/tools/metallography | 18013 | metallography_defect_detection_v1 | metallography | metallography_defect_detection |
| borescope | borescope | /opt/agent/vision-agent/tools/borescope | 18014 | borescope_defect_detection_v1 | borescope | borescope_defect_detection |
| hotsection | hotsection | /opt/agent/vision-agent/tools/hotsection | 18015 | infrared_defect_detection_v1 | infrared | infrared_defect_detection |
| fluorescence | fluorescence | /opt/agent/vision-agent/tools/fluorescence | 18016 | fpi_defect_detection_v1 | fpi | fpi_defect_detection |

真实工具入驻 = 同端口服务替换（mock 停、真工具起），平台侧只需
`switch_tool_endpoint.sh`（端点未变时甚至无需切换）。会议纪要 9/30 中期检查要求六场景
全部为真实 API——mock 是过渡脚手架，逐场景被替换。

## 2. 包结构与解耦层契约（决策 #5）

安装包（上传到服务器的目录）约定：

```
<pkg>/
  start.sh          必需。解耦层：前台 exec 启动服务；systemd 只认它，
                    不关心内部是 uvicorn、交付方脚本还是别的
  requirements.txt  在线安装的依赖清单（--offline 时可省；离线包由交付方安装器接管）
  tool.env          可选。额外环境变量（如 REPORT_FONT_PATH）；安装器会追加
                    HOST/PORT/TOOL_NAME 三键且以追加值为准
  <其余文件>        原样进 /opt/agent/vision-agent/tools/<tool>/app/
```

start.sh 契约：
- systemd `WorkingDirectory=/opt/agent/vision-agent/tools/<tool>`，`User=<tool>`（无 sudo、密码锁定）；
- `HOST/PORT/TOOL_NAME` 经 `tool.env`（EnvironmentFile）注入，脚本内只引用不硬编码；
- venv 固定 `../.venv`（相对 app/）或包内自带解释器布局（真实交付包自行适配）；
- 必须**前台**运行（`exec`），日志走 stdout/stderr → journald（`journalctl -u ai-tool@<tool>`）；
- 端口 >1024，无需任何 capability/setcap。

## 3. mock 实例快速入驻（Op-B，五场景）

本地组包（仓库根目录）：

```bash
mkdir -p /tmp/mock-pkg
cp mock_service/main.py /tmp/mock-pkg/
cp deploy/ai-tools/mock/start.sh deploy/ai-tools/mock/requirements.txt /tmp/mock-pkg/
tar -C /tmp/mock-pkg -czf /tmp/mock-pkg.tgz .
```

服务器端（逐实例，TOOL_NAME 用 §1 表映射）：

```bash
mkdir -p /root/mock-pkg && tar -C /root/mock-pkg -xzf /root/mock-pkg.tgz
bash /root/QualityCheckingAgent/deploy/install_ai_tool.sh xray /root/mock-pkg --tool-name dr_defect_detection
# …fracture/metallography/borescope/hotsection 同理（--tool-name 按表）
```

验收：`deploy/env-prep/verify_acceptance.sh`（`PORT_MODE=listen EXPECT_OS="NFS Server 3.1"`）
应报 18011-18016 端口监听且 /health 应答；平台侧 `GET /api/v1/capabilities/health` 全绿。

## 4. 真实工具入驻流程（每场景一遍，全程记录）

1. **收货核验**：SHA256 对照交付清单；权重哈希对照《验收对照记录》（防篡改/版本混淆，
   规范 §9：`model_weights_sha256` 可回填能力表）。
2. **兼容性预审**（glibc 2.17 wheel 底线）：
   - wheel 文件名含 `manylinux2014`/`manylinux_2_17` → 可用；
   - 仅 `manylinux_2_27/2_28`/`musllinux` → 不可用，需替换版本或裁剪依赖
     （已知案例：荧光交付包 pillow 12.3.0、contourpy 1.3.3 不兼容，见 §6 记录）；
   - `torch x.x.x+cpu linux_x86_64` 平标签 wheel：可装，但必须实跑
     `python -c "import torch"` 冒烟确认（交付方离线安装脚本第 7 步同款）。
3. **安装**：优先交付方离线安装器（保持与交付方验证环境一致），按 §2 契约适配
   BASE 路径/账户/端口/start.sh；差异全部记 §6。平台安装器（在线镜像）作备选。
4. **单机自检**（规范附录 C）：/health 形态、推理正常/异常用例（缺 task_id、坏图、
   错 Content-Type）、并发/超时行为、产物 URI 可达。
5. **平台侧适配确认**：
   - 响应外壳偏离规范（如 data 代替 result）→ `app/capabilities/adapters/tool_profiles.py`
     注册 profile（荧光 blade_defect_detector 已有，可作样板）；
   - `parameters` 契约（extra=forbid 的工具！）→ 能力 meta_json 的 default_params 必须
     与工具参数模型精确一致（荧光：part_id/device_id/timestamp，part_id 兜底 UNKNOWN）；
   - 图片 Content-Type：平台适配器已按扩展名派生正确 MIME（415 教训，2026-09-15）。
6. **端点切换**：`deploy/switch_tool_endpoint.sh <capability_id> http://127.0.0.1:<port> <health> '<meta_json>'`
   （meta_json 变更时给全量；随后提交真实任务 E2E 验证到 completed）。
7. **记录归档**：§6 模板 + `deploy/records/`；mock 实例停用（`systemctl disable --now
   ai-tool@<tool>` 前先确认新端点 E2E 通过）。

## 5. 运维命令速查

```bash
systemctl status|restart|stop ai-tool@<tool>       # 实例生命周期
journalctl -u ai-tool@<tool> -f                    # 实例日志
curl -s http://127.0.0.1:<port>/health             # 单机探活
curl -s http://127.0.0.1:80/api/v1/capabilities/health   # 平台视角巡检
ss -ltnp | grep 180                                # 端口监听核对
```

回滚（端点级）：`switch_tool_endpoint.sh` 切回 mock 端点（同端口实例仍在时甚至零操作）。
回滚（实例级）：`systemctl stop ai-tool@<tool>` + 恢复旧 app/（安装器幂等，重跑旧包即可）。

## 6. 偏差/差异记录（手册 §八 要求，随做随记）

| 日期 | 工具 | 事项 | 交付基线 | 实际部署 | 理由/影响 |
|---|---|---|---|---|---|
| 2026-09-15 | fluorescence | Python 版本 | 3.11.16（交付方验证环境） | 3.11.14（服务器既有） | 同 minor 版本，基线冻结不另装；本地 Mac 已用 3.11.14 全链路验证通过 |
| 2026-09-15 | fluorescence | pillow/contourpy | 12.3.0 / 1.3.3（manylinux_2_28） | 待入驻时替换：pillow→10.4.0；matplotlib 栈裁剪（不在运行时导入链） | glibc 2.17 装不上；PIL 在运行时链上必须保，matplotlib 仅离线 sweep 工具用 |
| 2026-09-15 | fluorescence | YOLOX 安装方式 | editable + 预编译 FastCOCOEvalOp | .pth 源码装载免编译 | 服务器 gcc 4.8.5 无法编译 torch 2.1.2 C++ 扩展；该算子仅 COCO 评估用，推理不依赖（本地已验证） |
| 2026-09-15 | fluorescence | 响应外壳 | 规范 §7 result 外壳 | 工具返回 data 外壳 | 平台侧 tool_profiles.py 归一（不改交付件）；作为规范偏差项反馈交付方 |
| 2026-09-15 | fluorescence | 错误码语义 | 规范/mock：1003=超大、2001=参数非法、4001=内部错误 | 工具：1003=图不可读、1004=超大、2001=模型未加载、4001=认证失败 | 平台按 HTTP 状态映射（413/415/422/503/504 语义与工具一致），错误分类不受影响；偏差项反馈交付方 |
| 2026-09-29 | 全部（本机 systemd 219） | 实例单元形态 | `ai-tool@<tool>` 模板单元（install_ai_tool.sh 起停 + health 轮询） | 模板实例 `203/EXEC`（ExecStart 的 `%i` 未展开），实际在跑的是手工铺的 per-tool 单元 `ai-tool-<tool>.service` | **假绿风险**：install_ai_tool.sh 的 restart 失败不致命，随后 `/health` 由端口上残留的旧进程应答，"health OK" 不能证明新包已接管。装完必须核对载荷身份（mock 扁平外壳 + `defect_domains` 恒 6；真工具按各自 /health 特征）再判成败。本机的启停一律用 per-tool 单元名 |
| 2026-09-29 | 平台侧（阿里云机） | `switch_tool_endpoint.sh` 第 3/4 步 | 脚本 `POST /api/v1/capabilities/reload` + 平台回读 | 该 API 受 AuthMiddleware 保护 → 401，脚本第 4 步 JSON 解析报错 | 落库（第 1/2 步）已成功，**不必重跑**：注册表按 MAX(version) 15s 轮询自动热生效；`version+1` 是生效凭证。要立即生效则 `systemctl restart quality-agent`。脚本需登录态才能做完平台侧回读，属已知缺口 |

（模板行：| YYYY-MM-DD | <tool> | <事项> | <交付基线> | <实际部署> | <理由/影响> |）
