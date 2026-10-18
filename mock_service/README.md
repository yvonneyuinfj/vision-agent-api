# 工业图像智能算法工具 Mock 服务说明文档

| 项目 | 内容 |
|------|------|
| 工具名称（tool_name） | `borescope_defect_detection`（孔探图像缺陷检测，Mock） |
| 文档版本 | v1.0.0（2026-08-18） |
| 对应服务版本 | tool_version `1.0.0` / model_version `1.0.0` / api_version `v1` |
| 参照标准 | 《（公开）动力所工业图像智能算法工具统一API 接入规范 V1.0》 |
| 适用对象 | 上层 AI 智能体 / 统一调度平台 / 接口联调与验收人员 |

> **重要说明**：本服务为 **Mock 实现**，不加载真实模型、不执行真实推理，仅用于接口联调与平台接入验证（下述"Mock 行为说明"章节描述了全部判定规则）。接入真实算法时按第 10 章指引替换模型加载与推理逻辑。

---

## 1. 概述

本服务以独立可部署的 REST API 形式，按统一接入规范实现对外服务入口，供上层智能体以一致方式调用。

**对外接口一览**

| 接口 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查（仅检查服务与模型可用状态，不执行推理） |
| `/api/v1/inference` | POST | 统一推理接口（`multipart/form-data`） |
| `/artifacts/{task_id}/{filename}` | GET | 标注图等衍生产物下载（Mock 存于内存，重启失效） |
| `/docs` | GET | Swagger 交互式接口文档（FastAPI 自带） |

**版本信息**：工具版本、模型版本、接口版本三者相互独立（规范 §17）：模型权重变化改 `model_version`，服务代码修复改 `tool_version`，破坏性接口变更升级 `v1`。

单进程六缺陷域：一个进程同时服务 borescope（crack/burn/corrosion）、
fracture（fatigue_crack/cleavage/dimple/intergranular）、
metallography（grain_coarsening/inclusion/decarburization/segregation）与
infrared / dr / fpi（真实模型未到位，仅域名与能力声明，推理走通用缺陷池）六域，
缺陷类型按文件名关键词路由；`/health` 与 `--identity` 声明 `defect_domains`。
六场景能力注册均指向同一服务端口（现 8002），接入真实模型时各自替换
能力注册表中的 endpoint 即可。

---

## 2. 环境与依赖要求

### 2.1 运行环境

| 项目 | 要求 |
|------|------|
| 操作系统 | Linux / macOS / Windows（开发与验证环境均可） |
| Python | **3.9 及以上**（开发验证环境：3.14） |
| 硬件 | **CPU-only**，无 GPU/CUDA 依赖（规范 §4 基线要求） |
| 外网 | 运行时**不访问公网**，无联网下载需求 |

### 2.2 Python 依赖清单（`requirements.txt`，版本已锁定）

| 依赖包 | 版本 | 用途 |
|--------|------|------|
| `fastapi` | 0.115.6 | Web 框架，提供路由、参数解析、统一异常处理 |
| `uvicorn[standard]` | 0.30.6 | ASGI 服务器，服务进程宿主 |
| `python-multipart` | 0.0.9 | `multipart/form-data` 文件与表单解析（FastAPI 上传必需） |
| `pillow` | 10.4.0 | 图像解码、尺寸/EXIF 校验、标注图生成 |

> 系统依赖：`pillow` 在部分精简 Linux 环境需 `libjpeg` 等系统库，安装异常时按发行版文档补齐。

---

## 3. 部署与运行

### 3.1 从干净环境部署

```bash
# 1. 创建并激活虚拟环境
python -m venv .venv
source .venv/bin/activate        # Linux / macOS
# .\.venv\Scripts\Activate.ps1  # Windows PowerShell

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动服务
python main.py
```

默认监听 `0.0.0.0:8002`。启动日志出现 `mock model loaded` 即表示服务就绪（规范 §13.4：启动后先完成模型加载，再通过 `/health` 暴露可用状态）。

### 3.2 停止服务

```bash
# 前台运行：Ctrl+C（正常退出，清理内存 artifact 与资源）
# 后台运行：
pkill -f "python main.py"
```

### 3.3 Docker 部署（可选）

如需容器化，在项目目录创建 `Dockerfile`：

```dockerfile
FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY main.py /app/main.py
ENV HOST=0.0.0.0 PORT=8002
EXPOSE 8002
CMD ["python", "main.py"]
```

```bash
docker build -t algorithm-mock:v1.0.0 .
docker run -d -p 8002:8002 --name algorithm-mock algorithm-mock:v1.0.0
docker stop algorithm-mock        # 停止
```

---

## 4. 配置项（环境变量）

所有部署参数通过环境变量注入，**代码中无硬编码路径或部署参数**（规范 §13.3）：

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `HOST` | `0.0.0.0` | 监听地址 |
| `PORT` | `8002` | 监听端口 |
| `TOOL_NAME` | `borescope_defect_detection` | 工具名称（随响应 `tool.tool_name` 返回） |
| `TOOL_VERSION` | `1.0.0` | 工具版本 |
| `MODEL_VERSION` | `1.0.0` | 模型版本 |
| `MAX_FILE_MB` | `50` | 图像最大文件大小（MB）（规范 §6 示例值） |
| `MAX_WIDTH` | `8192` | 图像最大宽（像素） |
| `MAX_HEIGHT` | `8192` | 图像最大高（像素） |
| `MAX_PIXELS` | `MAX_WIDTH×MAX_HEIGHT` | 最大像素总数（超过返回 1003/413） |

示例：

```bash
TOOL_NAME=borescope_defect_detection PORT=9000 python main.py
```

---

## 5. 接口说明

### 5.1 健康检查 `GET /health`

快速返回服务与模型可用状态，不执行推理。模型未加载完成时返回 `503` + 业务码 `3001`。

```bash
curl http://127.0.0.1:8002/health
```

```json
{
  "code": 0,
  "message": "success",
  "status": "healthy",
  "tool_name": "borescope_defect_detection",
  "tool_version": "1.0.0",
  "model_version": "1.0.0"
}
```

### 5.2 推理接口 `POST /api/v1/inference`

`Content-Type: multipart/form-data`，字符编码 UTF-8。

**请求字段**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `image` | File | 是 | 图像文件；**支持多图**——同名 `image` 字段重复提交，服务端逐张推理后合并结果（单图即 1 张） |
| `task_id` | String | 是 | 调用方生成的任务唯一编号，**原样返回** |
| `parameters` | JSON String | 否 | 算法可调参数，见 5.5 节 |
| `scene` | String | 否 | 检测场景编码；文件名关键词未命中时决定默认缺陷类型（如 metallography → 晶粒粗大） |
| `engine_id` | String | 否 | 发动机编号（公共扩展） |
| `component_id` | String | 否 | 部件编号/部位编号（公共扩展） |
| `image_id` | String | 否 | 图像业务编号（公共扩展） |
| `trace_id` | String | 否 | 链路追踪编号；未提供时由服务自动生成 |

**图像输入限制**（对应规范 §6）

| 项目 | 取值 |
|------|------|
| 支持格式 | JPEG/JPG、PNG、TIFF（`.jpg` / `.jpeg` / `.png` / `.tif` / `.tiff`） |
| 色彩模式 | 未限制（PIL 可解码的 RGB/Gray/16bit 等） |
| 最小分辨率 | 未设置下限 |
| 最大分辨率 | 宽 ≤ 8192、高 ≤ 8192、总像素 ≤ 8192×8192（超出返回 1003/413） |
| 最大文件大小 | 50 MB（超出返回 1003/413） |
| EXIF 方向 | 自动校正 |
| 异常图像 | 损坏/空文件返回统一输入错误码 1004/422 |

**响应统一外壳**（对应规范 §7）

| 一级字段 | 必填 | 说明 |
|----------|------|------|
| `code` | 是 | 业务状态码，`0` 表示成功 |
| `message` | 是 | 简要状态说明 |
| `task_id` | 是 | 与请求一致 |
| `trace_id` | 建议 | 便于日志追踪 |
| `tool` | 是 | `tool_name` / `tool_version` / `model_version` / `api_version` 版本信息 |
| `input` | 是 | 实际处理输入的关键元信息（`image_name` / `width` / `height` / 扩展字段） |
| `performance` | 是 | 本次处理总耗时 `processing_time_ms` |
| `result` | 是 | 算法结构化结果（见下） |
| `artifacts` | 否 | 标注图等衍生产物元信息 |

**`result` 字段约定**（对应规范 §7.1）

- 检测类工具返回 `defects` 数组；缺陷类别同时给出稳定英文编码 `defect_type` 与中文名称 `defect_name_cn`。
- `confidence` 为 0~1 浮点数；`severity` 取值 `low` / `medium` / `high`。
- `location.bbox` 定义为 `[x_min, y_min, x_max, y_max]`，原点位于图像左上角，单位像素。
- `status` 取值 `normal` / `abnormal`，对应 `summary` 为中文摘要。

**`artifacts` 与下载**（对应规范 §7.2）

```json
"artifacts": [
  {
    "artifact_type": "annotated_image",
    "mime_type": "image/png",
    "name": "abnormal_crack_annotated.png",
    "uri": "/artifacts/TASK202608140002/abnormal_crack_annotated.png"
  }
]
```

通过 `uri` 下载标注图。接口对文件名做了路径穿越防护，非法文件名返回统一 JSON 错误。

### 5.3 算法参数 `parameters`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `confidence_threshold` | float | `0.5` | 置信度阈值，范围 0~1，越界返回 2001 |
| `max_defects` | int | `1` | 最大返回缺陷数，范围 1~10（当前 Mock 实际最多生成 3 处） |
| `return_annotated` | bool | `false` | 是否生成并返回标注图 artifact |
| `mock_status` | string | `null` | 强制指定 `normal` / `abnormal`，**仅用于 Mock 测试** |

> `mock_status` 为本工具 Mock 阶段扩展参数，接入真实算法时移除；按要求在配套填报Excel中完整列出。

### 5.4 错误码与异常处理（对应规范 §8）

| 业务码 | HTTP状态 | 名称 | 说明 | Mock 状态 |
|--------|----------|------|------|-----------|
| 0 | 200 | SUCCESS | 处理成功 | ✅ |
| 1001 | 400 | INVALID_REQUEST | 缺少必填字段或参数格式错误 | ✅ |
| 1002 | 415 | UNSUPPORTED_IMAGE_FORMAT | 不支持的图像格式/通道 | ✅ |
| 1003 | 413 | IMAGE_TOO_LARGE | 文件大小或分辨率超过限制 | ✅ |
| 1004 | 422 | INVALID_IMAGE | 图像损坏、无法解码或内容无效 | ✅ |
| 2001 | 422 | INVALID_PARAMETER | 算法参数越界或组合不合法 | ✅ |
| 3001 | 503 | MODEL_NOT_READY | 模型未加载或初始化失败 | ✅ |
| 3002 | 500 | INFERENCE_FAILED | 模型推理执行失败 | 预留，Mock 不触发 |
| 3003 | 504 | INFERENCE_TIMEOUT | 推理超过服务超时 | 预留，Mock 不触发 |
| 4001 | 500 | INTERNAL_ERROR | 未分类内部错误 | ✅ |

所有错误响应均为统一 JSON 外壳（`code` / `message` / `task_id` / `trace_id` / `result: null`），**不返回纯文本或 HTML 错误页**；未匹配路由（404）、方法不允许（405）等框架直出错误也由兜底中间件统一转换。

---

## 6. Mock 行为说明

当前 Mock 的判定与模拟逻辑（非真实算法，仅用于接口联调）：

1. **结果判定**（二选一命中即返回 `abnormal`）：
   - `parameters.mock_status = "abnormal"`（显式指定 `normal` 时恒返回正常）；
   - 文件名包含关键词 `abnormal` / `defect` / `crack` / `burn` / `corrosion` 之一；
   - 其余情况返回 `normal`（`defects: []`）。
2. **缺陷生成**：按文件名关键词选择主缺陷类型（`crack` / `burn` / `corrosion`），每次请求随机生成缺陷数量（1 ~ `max_defects`，上限 3）、置信度、严重度、位置与非首缺陷类型，模拟真实推理结果的多样性（同一请求参数重复调用，结果内容不同）。
3. **模拟推理耗时**：0.02 ~ 0.2 秒（随文件大小线性映射）。
4. **标注图 artifact**：存于进程内存，最多保留 100 个（超出淘汰最早者），**服务重启后全部失效**。

---

## 7. 调用示例

### 7.1 正常图像

```bash
curl -X POST http://127.0.0.1:8002/api/v1/inference \
  -F "task_id=TASK202608140001" \
  -F "image=@normal.jpg" \
  -F "engine_id=ENG001" \
  -F "component_id=HPT-BLADE-01" \
  -F "image_id=IMG001" \
  -F 'parameters={"confidence_threshold": 0.5, "max_defects": 2}'
```

```json
{
  "code": 0,
  "message": "success",
  "task_id": "TASK202608140001",
  "trace_id": "trace-xxxx",
  "tool": {
    "tool_name": "borescope_defect_detection",
    "tool_version": "1.0.0",
    "model_version": "1.0.0",
    "api_version": "v1"
  },
  "input": {
    "image_name": "normal.jpg",
    "width": 1024,
    "height": 1024,
    "engine_id": "ENG001",
    "component_id": "HPT-BLADE-01",
    "image_id": "IMG001"
  },
  "performance": {
    "processing_time_ms": 35
  },
  "result": {
    "status": "normal",
    "summary": "未发现明显异常",
    "defects": []
  },
  "artifacts": []
}
```

### 7.2 异常图像 + 标注图 artifact

```bash
curl -X POST http://127.0.0.1:8002/api/v1/inference \
  -F "task_id=TASK202608140002" \
  -F "image=@abnormal_crack.jpg" \
  -F "trace_id=TRACE-001" \
  -F 'parameters={"confidence_threshold": 0.6, "max_defects": 2, "return_annotated": true}'
```

```json
{
  "code": 0,
  "message": "success",
  "task_id": "TASK202608140002",
  "trace_id": "TRACE-001",
  "tool": {
    "tool_name": "borescope_defect_detection",
    "tool_version": "1.0.0",
    "model_version": "1.0.0",
    "api_version": "v1"
  },
  "input": {
    "image_name": "abnormal_crack.jpg",
    "width": 1024,
    "height": 1024,
    "engine_id": null,
    "component_id": null,
    "image_id": null
  },
  "performance": {
    "processing_time_ms": 43
  },
  "result": {
    "status": "abnormal",
    "summary": "检测到2处疑似缺陷",
    "defects": [
      {
        "defect_id": "D001",
        "defect_type": "crack",
        "defect_name_cn": "裂纹",
        "confidence": 0.85,
        "severity": "medium",
        "location": {"type": "bbox", "bbox": [102, 153, 358, 430]},
        "description": "检测到疑似裂纹区域"
      },
      {
        "defect_id": "D002",
        "defect_type": "corrosion",
        "defect_name_cn": "腐蚀",
        "confidence": 0.95,
        "severity": "high",
        "location": {"type": "bbox", "bbox": [184, 235, 440, 512]},
        "description": "检测到疑似腐蚀区域"
      }
    ]
  },
  "artifacts": [
    {
      "artifact_type": "annotated_image",
      "mime_type": "image/png",
      "name": "abnormal_crack_annotated.png",
      "uri": "/artifacts/TASK202608140002/abnormal_crack_annotated.png"
    }
  ]
}
```

> 缺陷的置信度/数量/位置为 Mock 随机生成（每次请求不同），随 `parameters` 变化；响应结构固定如上。

### 7.3 不支持的图像格式

```bash
echo "test" > bad.bmp
curl -X POST http://127.0.0.1:8002/api/v1/inference \
  -F "task_id=TASK202608140003" -F "image=@bad.bmp"
```

```json
{
  "code": 1002,
  "message": "unsupported image format: bmp",
  "task_id": "TASK202608140003",
  "trace_id": "trace-xxxx",
  "result": null
}
```

### 7.4 参数越界

```bash
curl -X POST http://127.0.0.1:8002/api/v1/inference \
  -F "task_id=TASK202608140004" -F "image=@normal.jpg" \
  -F 'parameters={"confidence_threshold": 1.2}'
```

```json
{
  "code": 2001,
  "message": "confidence_threshold must be between 0 and 1",
  "task_id": "TASK202608140004",
  "trace_id": "trace-xxxx",
  "result": null
}
```

### 7.5 测试图像生成

```bash
python - <<'PY'
from PIL import Image

Image.new("RGB", (1024, 1024), (40, 80, 120)).save("normal.jpg")
Image.new("RGB", (1024, 1024), (120, 40, 40)).save("abnormal_crack.jpg")
PY
```

---

## 8. 日志与可观测性（对应规范 §12）

- 日志输出至标准输出（stdout），格式：`时间 级别 [工具名] 消息`。
- 每条请求至少记录：`time`、`level`、`tool_name`、`task_id`、`trace_id`、接口、处理耗时、结果状态、异常摘要。
- **不记录完整原始图像内容或 Base64**；文件名记录为剥离路径后的安全文件名。
- 异常携带完整错误信息（开发模式含堆栈）。
- 说明：Mock 阶段未配置日志文件路径与滚动策略；交付真实工具时须补充（路径、单文件大小、保留周期、磁盘上限）。

---

## 9. 验收自测指引

按以下顺序自测（对应规范 §16 验收点）：

```bash
# 1. 健康检查（启动后应可直接访问）
curl http://127.0.0.1:8002/health

# 2. 正常图像 → code 0 / result.status normal
curl -X POST http://127.0.0.1:8002/api/v1/inference \
  -F "task_id=TASK001" -F "image=@normal.jpg"

# 3. 异常图像 → code 0 / result.status abnormal
curl -X POST http://127.0.0.1:8002/api/v1/inference \
  -F "task_id=TASK002" -F "image=@abnormal_crack.jpg"

# 4. 非法输入：不支持格式 → 1002/415
echo test > bad.bmp
curl -X POST http://127.0.0.1:8002/api/v1/inference \
  -F "task_id=TASK003" -F "image=@bad.bmp"

# 5. 非法输入：参数越界 → 2001/422
curl -X POST http://127.0.0.1:8002/api/v1/inference \
  -F "task_id=TASK004" -F "image=@normal.jpg" \
  -F 'parameters={"confidence_threshold": 2}'

# 6. 非法输入：缺失必填字段 → 1001/400
curl -X POST http://127.0.0.1:8002/api/v1/inference -F "image=@normal.jpg"

# 7. 失败响应必须为 JSON（未匹配路径也应返回统一 JSON 外壳）
curl http://127.0.0.1:8002/api/v1/not-exist
```

---

## 10. 替换为真实算法

接入真实模型时仅需替换三处，接口外壳与错误处理保持不变：

1. **模型加载**——`main.py` 的 `lifespan` 中加载模型并设置 `state.model_ready`：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # model = load_your_model(os.getenv("MODEL_PATH", "/models/xxx.onnx"))
    state.model_ready = True
    yield
    state.model_ready = False
```

2. **图像预处理**——`validate_image` 之后可增加解码→缩放→归一化→张量化流程（当前实现已含格式/大小/分辨率校验与 EXIF 校正）。

3. **推理逻辑**——以真实推理替换 `build_result(...)`，`result` 保持下列结构：

```json
{
  "status": "abnormal",
  "summary": "检测到1处疑似裂纹缺陷",
  "defects": [
    {
      "defect_id": "D001",
      "defect_type": "crack",
      "defect_name_cn": "裂纹",
      "confidence": 0.932,
      "severity": "medium",
      "location": {"type": "bbox", "bbox": [120, 230, 460, 580]},
      "description": "叶片区域存在疑似裂纹"
    }
  ]
}
```

真实 `defects` 字段（`defect_type` 等）须同时给出英文稳定编码，`severity` 不支持时返回 `null`。

---

## 11. 常见故障排查

| 现象 | 可能原因 | 处理方式 |
|------|----------|----------|
| 启动报 `Address already in use` | 端口被占用 | 设置 `PORT` 更换端口后重启 |
| 推理返回 `1001` + `task_id: Field required` | 请求缺少必填字段 | 检查 `task_id`、`image` 是否随表单提交 |
| 返回 `1002` | 图像格式不支持 | 使用 `.jpg/.jpeg/.png/.tif/.tiff` 格式 |
| 返回 `1003/413` | 文件或分辨率超限 | 调整 `MAX_FILE_MB`/`MAX_WIDTH`/`MAX_HEIGHT` |
| 返回 `2001` | `parameters` 非法或参数越界 | 按 5.3 节字段表检查取值范围 |
| `/health` 返回 `503/3001` | 模型未就绪 | Mock 正常启动后即就绪；检查启动日志中 `model loaded` 行 |
| artifact 下载 404 | 服务重启后内存数据丢失 | 重新调用推理生成（`return_annotated: true`） |
| 返回 `4001` + `Not Found` | 请求了不存在的接口 | 检查 URL 路径（应为 `/api/v1/inference`） |
| 请求返回非 JSON 内容 | 代理/网关层干扰 | 确认直连服务端口；本服务自身不产生 HTML 错误页 |

---

## 12. 合规对照（附录C 自检清单）

**已满足**：干净环境可部署、`/health` 与 `/api/v1/inference` 可用、全部响应为 JSON（含 404/405 兜底）、统一错误码、版本可追溯、无 GPU/CUDA 依赖、无硬编码路径与账号口令、依赖版本锁定、日志可经 task_id/trace_id 定位。

**Mock 阶段待完善**（交付真实工具时补齐）：性能基准测试（含 CPU 型号、图像规格、Avg/P95/Max、内存峰值）、技术信息填报 Excel（含图像输入规格、`mock_status` 扩展参数说明）、日志文件滚动策略、100 样本连续稳定性测试报告、模型权重校验值（SHA256）。

---

## 13. 版本变更记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0.0 | 2026-08-18 | 首版：对齐统一接入规范 V1.0 的接口外壳、错误码、部署与配置说明；新增 404/405 统一 JSON 兜底、严格布尔参数校验的说明 |