from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import random
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional, Tuple

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint

try:
    from PIL import Image, ImageDraw, ImageOps

    PIL_AVAILABLE = True
except Exception:  # pragma: no cover
    Image = None
    ImageDraw = None
    ImageOps = None
    PIL_AVAILABLE = False


# =========================
# 服务基础信息
# =========================

TOOL_NAME = os.getenv("TOOL_NAME", "borescope_defect_detection")
TOOL_VERSION = os.getenv("TOOL_VERSION", "1.0.0")
MODEL_VERSION = os.getenv("MODEL_VERSION", "1.0.0")
API_VERSION = "v1"

# 输入限制，可根据实际算法工具修改
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "50"))
MAX_WIDTH = int(os.getenv("MAX_WIDTH", "8192"))
MAX_HEIGHT = int(os.getenv("MAX_HEIGHT", "8192"))
MAX_PIXELS = int(os.getenv("MAX_PIXELS", str(MAX_WIDTH * MAX_HEIGHT)))

# 规范 §12：日志是否记录发动机/部件编号等业务元数据（默认关，内网按需开启）
LOG_BUSINESS_META = os.getenv("LOG_BUSINESS_META", "").strip().lower() in {"1", "true", "yes", "on"}

# =========================
# 缺陷域配置（单进程多域：系统六场景共用同一服务与端口 8002）
# infrared / dr / fpi 真实模型未到位，暂不设专属缺陷池，推理走通用池；
# DEFECT_DOMAINS 用于 /health 与 --identity 的域名声明 + 能力注册校验
# =========================

DEFECT_DOMAINS: list[str] = ["borescope", "infrared", "dr", "fpi", "fracture", "metallography"]

TOOL_SPEC: dict[str, Any] = {
    "defect_names": {
        "crack": "裂纹",
        "burn": "烧伤",
        "corrosion": "腐蚀",
        "fatigue_crack": "疲劳裂纹",
        "cleavage": "解理",
        "dimple": "韧窝",
        "intergranular": "沿晶",
        "grain_coarsening": "晶粒粗大",
        "inclusion": "夹杂",
        "decarburization": "脱碳",
        "segregation": "偏析",
    },
    # 顺序敏感：fatigue_crack 含子串 crack，fracture 组必须先于 borescope 组匹配；
    # metallography 组关键词与既有组无子串冲突，插入两组之间
    "primary_keywords": (
        ("fatigue", "fatigue_crack"),
        ("cleavage", "cleavage"),
        ("dimple", "dimple"),
        ("intergranular", "intergranular"),
        ("grain", "grain_coarsening"),
        ("inclusion", "inclusion"),
        ("decarb", "decarburization"),
        ("segregat", "segregation"),
        ("burn", "burn"),
        ("corrosion", "corrosion"),
    ),
    "primary_default": "crack",
    "abnormal_keywords": (
        "abnormal", "defect", "fracture",
        "fatigue", "cleavage", "dimple", "intergranular",
        "crack", "burn", "corrosion",
        "metallography", "grain", "inclusion", "decarburization", "segregation",
    ),
    "type_cycle": (
        "crack", "burn", "corrosion",
        "fatigue_crack", "cleavage", "dimple", "intergranular",
        "grain_coarsening", "inclusion", "decarburization", "segregation",
    ),
}

# 场景默认缺陷（文件名关键词未命中时按调用方下发的 scene 回退）：
# 金相场景不得落回 borescope 的 crack；未声明场景保持全局默认 primary_default
SCENE_PRIMARY_DEFAULTS: dict[str, str] = {
    "metallography": "grain_coarsening",
}

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

DEFAULT_CONFIDENCE_THRESHOLD = 0.5
DEFAULT_MAX_DEFECTS = 1

if PIL_AVAILABLE:
    # 避免 PIL 对超大图触发 DecompressionBombWarning/Error
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(TOOL_NAME)


# =========================
# 服务状态
# =========================

class ToolState:
    def __init__(self) -> None:
        self.model_ready: bool = False
        self.load_error: Optional[str] = None


state = ToolState()

# 仅用于 mock artifact 演示，生产环境应落盘或对象存储
ARTIFACTS: Dict[str, bytes] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    模拟服务启动时加载模型。
    真实接入时，可在这里加载 ONNX / PyTorch / OpenVINO 等模型。
    """
    try:
        await asyncio.sleep(0.01)
        state.model_ready = True
        logger.info(
            "mock model loaded, tool_name=%s, tool_version=%s, model_version=%s",
            TOOL_NAME,
            TOOL_VERSION,
            MODEL_VERSION,
        )
    except Exception as exc:
        state.model_ready = False
        state.load_error = str(exc)
        logger.exception("model load failed")

    yield

    state.model_ready = False
    ARTIFACTS.clear()
    logger.info("service stopped")


app = FastAPI(
    title=f"{TOOL_NAME} Mock API",
    version=TOOL_VERSION,
    lifespan=lifespan,
)


# =========================
# 统一错误返回
# =========================

ErrorTuple = Tuple[int, str, int]


def make_error(
    code: int,
    message: str,
    http_status: int,
    task_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> JSONResponse:
    """
    统一错误响应结构。
    """
    payload = {
        "code": code,
        "message": message,
        "task_id": task_id,
        "trace_id": trace_id,
        "result": None,
    }
    return JSONResponse(status_code=http_status, content=payload)


def validation_error_message(exc: RequestValidationError) -> str:
    messages = []
    for err in exc.errors():
        loc = ".".join(str(item) for item in err.get("loc", []))
        msg = err.get("msg", "invalid")
        messages.append(f"{loc}: {msg}")
    return "; ".join(messages) or "invalid request"


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """
    FastAPI 参数校验失败，统一转为 1001 INVALID_REQUEST。
    """
    return make_error(
        code=1001,
        message=validation_error_message(exc),
        http_status=400,
    )


def business_code_for_status(status: int) -> int:
    """
    HTTP 状态码 → 规范业务码（§8 错误码表）。
    规范未覆盖的 404/405 等状态归为 4001 未分类内部错误，HTTP 状态原样保留。
    """
    if status == 400:
        return 1001
    if status == 413:
        return 1003
    if status == 415:
        return 1002
    if status == 422:
        return 2001
    if status == 503:
        return 3001
    if status == 504:
        return 3003

    return 4001


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    """
    FastAPI/Starlette HTTPException 统一转 JSON，避免返回 HTML/纯文本错误页。
    """
    return make_error(
        code=business_code_for_status(exc.status_code),
        message=str(exc.detail),
        http_status=exc.status_code,
    )


@app.exception_handler(Exception)
async def handle_unexpected_exception(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """
    未捕获异常统一返回 4001 INTERNAL_ERROR。
    """
    logger.exception("unhandled exception")
    return make_error(
        code=4001,
        message="internal error",
        http_status=500,
    )


@app.middleware("http")
async def unified_error_middleware(
    request: Request,
    call_next: RequestResponseEndpoint,
) -> Response:
    """
    兜底：未匹配路由（404）、方法不允许（405）等由框架直出的纯文本/HTML
    错误响应，统一转为规范 JSON 外壳（§7"失败时不得返回纯文本或 HTML 错误页"）。
    """
    response = await call_next(request)

    content_type = response.headers.get("content-type", "")
    if response.status_code < 400 or content_type.startswith("application/json"):
        return response

    body = b"".join([chunk async for chunk in response.body_iterator])
    message = body.decode("utf-8", "replace").strip() or "request failed"

    return make_error(
        code=business_code_for_status(response.status_code),
        message=message,
        http_status=response.status_code,
    )


# =========================
# 参数解析
# =========================

def parse_bool(value: Any) -> bool:
    """
    严格解析布尔值：仅接受布尔或明确字符串（true/1/yes/y/on 等）。
    数字等其他类型一律拒绝（由调用方返回 2001 INVALID_PARAMETER），
    避免 0.5、2 等越界值被静默当作 true。
    """
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "1", "yes", "y", "on"}:
            return True
        if v in {"false", "0", "no", "n", "off"}:
            return False

    raise ValueError("invalid boolean value")


def validate_parameters(raw: Optional[str]) -> Tuple[Dict[str, Any], Optional[ErrorTuple]]:
    """
    解析 parameters JSON String。

    支持的 mock 参数：
    - confidence_threshold: float, 0~1, default 0.5
    - max_defects: int, 1~10, default 1
    - return_annotated: bool, default false
    - mock_status: normal / abnormal, 仅用于 mock 测试
    - mock_severity: low / medium / high, 仅用于 mock 测试（确定性覆盖全部缺陷严重度）
    - mock_error: bool, 仅用于 mock 测试（true 时短路推理，模拟模型侧故障）
    """
    params: Dict[str, Any] = {
        "confidence_threshold": DEFAULT_CONFIDENCE_THRESHOLD,
        "max_defects": DEFAULT_MAX_DEFECTS,
        "return_annotated": False,
        "mock_status": None,
        "mock_severity": None,
        "mock_error": False,
    }

    if raw is None or raw.strip() == "":
        return params, None

    try:
        data = json.loads(raw)
    except Exception:
        return {}, (2001, "parameters is not valid JSON", 422)

    if not isinstance(data, dict):
        return {}, (2001, "parameters must be a JSON object", 422)

    if "confidence_threshold" in data:
        try:
            threshold = float(data["confidence_threshold"])
        except Exception:
            return {}, (2001, "confidence_threshold must be a number", 422)

        if threshold < 0.0 or threshold > 1.0:
            return {}, (2001, "confidence_threshold must be between 0 and 1", 422)

        params["confidence_threshold"] = threshold

    if "max_defects" in data:
        try:
            max_defects = int(data["max_defects"])
        except Exception:
            return {}, (2001, "max_defects must be an integer", 422)

        if max_defects < 1 or max_defects > 10:
            return {}, (2001, "max_defects must be between 1 and 10", 422)

        params["max_defects"] = max_defects

    if "return_annotated" in data:
        try:
            params["return_annotated"] = parse_bool(data["return_annotated"])
        except Exception:
            return {}, (2001, "return_annotated must be a boolean", 422)

    if "mock_status" in data:
        mock_status = data["mock_status"]
        if isinstance(mock_status, str) and mock_status.strip() == "":
            mock_status = None

        if mock_status not in (None, "normal", "abnormal"):
            return {}, (2001, "mock_status must be normal or abnormal", 422)

        params["mock_status"] = mock_status

    if "mock_severity" in data:
        mock_severity = data["mock_severity"]
        if isinstance(mock_severity, str) and mock_severity.strip() == "":
            mock_severity = None

        if mock_severity not in (None, "low", "medium", "high"):
            return {}, (2001, "mock_severity must be low, medium or high", 422)

        params["mock_severity"] = mock_severity

    if "mock_error" in data:
        try:
            params["mock_error"] = parse_bool(data["mock_error"])
        except Exception:
            return {}, (2001, "mock_error must be a boolean", 422)

    return params, None


# =========================
# 图像校验
# =========================

def validate_image(
    filename: Optional[str],
    content: bytes,
) -> Tuple[Optional[Dict[str, Any]], Optional[ErrorTuple]]:
    """
    校验图像格式、大小、分辨率，并读取宽高。
    """
    ext = os.path.splitext(filename or "")[1].lower()

    if ext not in SUPPORTED_EXTENSIONS:
        ext_name = ext.lstrip(".") if ext else "unknown"
        return None, (1002, f"unsupported image format: {ext_name}", 415)

    if len(content) == 0:
        return None, (1004, "empty image file", 422)

    if len(content) > MAX_FILE_MB * 1024 * 1024:
        return None, (1003, f"image file exceeds {MAX_FILE_MB} MB", 413)

    if PIL_AVAILABLE:
        try:
            # 第一次打开用于 verify
            bio = io.BytesIO(content)
            with Image.open(bio) as im:
                im.verify()

            # 第二次打开读取尺寸
            bio = io.BytesIO(content)
            with Image.open(bio) as im:
                # 自动处理 EXIF 方向
                im = ImageOps.exif_transpose(im)
                width, height = im.size
                color_mode = im.mode

            if width <= 0 or height <= 0:
                raise ValueError("invalid image size")

            if width > MAX_WIDTH or height > MAX_HEIGHT or width * height > MAX_PIXELS:
                return None, (
                    1003,
                    f"image resolution exceeds limit: {width}x{height}",
                    413,
                )

        except Exception as exc:
            if exc.__class__.__name__ == "DecompressionBombError":
                return None, (1003, "image resolution exceeds limit", 413)

            return None, (1004, "invalid image: cannot decode", 422)
    else:
        # 如果没有 PIL，则只做协议 mock，不做真实图像解码
        width = 1024
        height = 1024
        color_mode = "unknown"

    return {
        "width": width,
        "height": height,
        "color_mode": color_mode,
    }, None


# =========================
# Mock 推理结果
# =========================


def clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def choose_primary_defect(filename: str, scene: Optional[str] = None) -> str:
    """主缺陷类型：文件名关键词优先；未命中按场景默认回退；无场景声明走全局默认。"""
    name = (filename or "").lower()

    for keyword, defect_type in TOOL_SPEC["primary_keywords"]:
        if keyword in name:
            return defect_type

    return SCENE_PRIMARY_DEFAULTS.get((scene or "").lower(), TOOL_SPEC["primary_default"])


def is_abnormal(filename: str, params: Dict[str, Any]) -> bool:
    """
    Mock 规则：
    1. parameters.mock_status 可强制指定 normal / abnormal
    2. 文件名包含 abnormal / defect / crack / burn / corrosion 时返回 abnormal
    3. 其他情况返回 normal
    """
    mock_status = params.get("mock_status")

    if mock_status == "normal":
        return False

    if mock_status == "abnormal":
        return True

    name = (filename or "").lower()
    keywords = TOOL_SPEC["abnormal_keywords"]
    return any(keyword in name for keyword in keywords)


def build_result(
    filename: str,
    width: int,
    height: int,
    params: Dict[str, Any],
    scene: Optional[str] = None,
) -> Dict[str, Any]:
    """
    构造结构化 result。

    Mock 策略：abnormal 时每次请求随机生成缺陷数量、位置、置信度、严重度
    与非首缺陷类型，模拟真实推理结果的多样性；显式参数仍然优先——
    数量不超过 max_defects（上限 3），confidence 不低于 confidence_threshold。
    """
    defect_names = TOOL_SPEC["defect_names"]

    if not is_abnormal(filename, params):
        return {
            "status": "normal",
            "summary": "未发现明显异常",
            "defects": [],
        }

    threshold = float(params["confidence_threshold"])
    max_defects = min(int(params["max_defects"]), 3)
    primary_type = choose_primary_defect(filename, scene)

    type_cycle = TOOL_SPEC["type_cycle"]
    severity_cycle = ["low", "medium", "high"]

    defect_count = random.randint(1, max_defects)
    defects = []

    for i in range(defect_count):
        defect_type = primary_type if i == 0 else random.choice(type_cycle)

        confidence = round(
            clamp(
                threshold + random.uniform(0.02, 0.30),
                0.0,
                0.99,
            ),
            3,
        )

        # 随机框位置：左上角落在画幅前半区，右下角至少偏移 15% 画幅，保证框合法
        x_ratio = random.uniform(0.05, 0.55)
        y_ratio = random.uniform(0.08, 0.50)
        x1 = int(width * x_ratio)
        y1 = int(height * y_ratio)
        x2 = int(width * random.uniform(x_ratio + 0.15, 0.98))
        y2 = int(height * random.uniform(y_ratio + 0.15, 0.98))

        # mock_severity 联调参数优先：确定性覆盖严重度（平台判级/复核门演示需要可复现）
        severity = params.get("mock_severity") or random.choice(severity_cycle)

        defects.append(
            {
                "defect_id": f"D{i + 1:03d}",
                "defect_type": defect_type,
                "defect_name_cn": defect_names.get(defect_type, "未知缺陷"),
                "confidence": confidence,
                "severity": severity,
                "location": {
                    "type": "bbox",
                    "bbox": [x1, y1, x2, y2],
                },
                "description": f"检测到疑似{defect_names.get(defect_type, '缺陷')}区域",
            }
        )

    return {
        "status": "abnormal",
        "summary": f"检测到{len(defects)}处疑似缺陷",
        "defects": defects,
    }


# =========================
# Mock 标注图
# =========================

def generate_annotated_png(content: bytes, result: Dict[str, Any]) -> Optional[bytes]:
    """
    生成简单标注图。
    """
    if not PIL_AVAILABLE:
        return None

    try:
        img = Image.open(io.BytesIO(content))
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")

        draw = ImageDraw.Draw(img)
        line_width = max(2, min(img.width, img.height) // 250)

        for defect in result.get("defects", []):
            location = defect.get("location") or {}
            if location.get("type") != "bbox":
                continue

            bbox = location.get("bbox")
            if not bbox or len(bbox) != 4:
                continue

            draw.rectangle(bbox, outline=(255, 0, 0), width=line_width)

            label = f'{defect.get("defect_type", "defect")} {defect.get("confidence", 0):.2f}'
            draw.text(
                (bbox[0] + 2, max(0, bbox[1] - 12)),
                label,
                fill=(255, 0, 0),
            )

        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()

    except Exception:
        logger.exception("annotated artifact generation failed")
        return None


def store_artifact(task_id: str, name: str, payload: bytes) -> str:
    """
    将 artifact 存入内存字典，仅用于 mock。
    """
    key = f"{task_id}/{name}"
    ARTIFACTS[key] = payload

    # 简单限制内存 artifact 数量
    if len(ARTIFACTS) > 100:
        try:
            ARTIFACTS.pop(next(iter(ARTIFACTS)))
        except StopIteration:
            pass

    return f"/artifacts/{key}"


# =========================
# 接口：健康检查
# =========================

@app.get("/health")
async def health() -> JSONResponse:
    """
    健康检查接口。
    不执行完整推理。
    """
    if not state.model_ready:
        return make_error(
            code=3001,
            message=state.load_error or "model not ready",
            http_status=503,
        )

    return JSONResponse(
        status_code=200,
        content={
            "code": 0,
            "message": "success",
            "status": "healthy",
            "tool_name": TOOL_NAME,
            "tool_version": TOOL_VERSION,
            "model_version": MODEL_VERSION,
            "defect_domains": DEFECT_DOMAINS,
        },
    )


# =========================
# 接口：推理
# =========================

@app.post("/api/v1/inference")
async def inference(
    image: list[UploadFile] = File(...),
    task_id: str = Form(...),
    parameters: Optional[str] = Form(default=None),
    scene: Optional[str] = Form(default=None),
    engine_id: Optional[str] = Form(default=None),
    component_id: Optional[str] = Form(default=None),
    image_id: Optional[str] = Form(default=None),
    trace_id: Optional[str] = Form(default=None),
) -> JSONResponse:
    """
    统一推理接口（多图逐张推理：同名 image 字段可重复提交，单图为 1 元素列表）。

    请求类型：multipart/form-data

    字段：
    - image: 必填，图像文件（可多个，逐张推理后合并结果）
    - task_id: 必填，任务 ID
    - parameters: 可选，JSON 字符串
    - scene: 可选，检测场景编码（文件名关键词未命中时决定默认缺陷类型）
    - engine_id: 可选
    - component_id: 可选
    - image_id: 可选
    - trace_id: 可选
    """
    start_time = time.perf_counter()

    task_id = task_id.strip() if task_id else ""
    trace = trace_id.strip() if trace_id else f"trace-{uuid.uuid4().hex[:16]}"

    if not task_id:
        return make_error(
            code=1001,
            message="task_id is required",
            http_status=400,
            task_id=None,
            trace_id=trace,
        )

    logger.info(
        "inference start, task_id=%s, trace_id=%s, images=%d, scene=%s",
        task_id,
        trace,
        len(image),
        scene or "-",
    )
    if LOG_BUSINESS_META:
        logger.info(
            "business meta, task_id=%s, engine_id=%s, component_id=%s, image_id=%s",
            task_id,
            engine_id or "-",
            component_id or "-",
            image_id or "-",
        )

    if not state.model_ready:
        return make_error(
            code=3001,
            message=state.load_error or "model not ready",
            http_status=503,
            task_id=task_id,
            trace_id=trace,
        )

    # 解析算法参数
    params, param_error = validate_parameters(parameters)
    if param_error:
        code, message, http_status = param_error
        logger.warning(
            "parameter error, task_id=%s, trace_id=%s, code=%s, message=%s",
            task_id,
            trace,
            code,
            message,
        )
        return make_error(
            code=code,
            message=message,
            http_status=http_status,
            task_id=task_id,
            trace_id=trace,
        )

    # 联调短路：mock_error 强制模拟模型侧推理失败（平台失败链路/错误报告演示）
    if params.get("mock_error"):
        logger.warning("mock_error requested: simulated model failure, task_id=%s", task_id)
        return make_error(
            code=3002,
            message="模拟模型侧推理失败（mock_error）",
            http_status=500,
            task_id=task_id,
            trace_id=trace,
        )

    # ---- 逐张读取 / 校验 / 推理（任一图非法即整体拒绝，与单图时代语义一致） ----
    per_image: list[dict] = []
    for index, upload in enumerate(image):
        safe_name = os.path.basename(upload.filename or f"upload_{index}")
        content = await upload.read()

        image_meta, image_error = validate_image(upload.filename, content)
        if image_error:
            code, message, http_status = image_error
            logger.warning(
                "image error, task_id=%s, trace_id=%s, image=%s, code=%s, message=%s",
                task_id,
                trace,
                safe_name,
                code,
                message,
            )
            return make_error(
                code=code,
                message=message,
                http_status=http_status,
                task_id=task_id,
                trace_id=trace,
            )

        # 模拟推理耗时
        await asyncio.sleep(min(0.2, max(0.02, len(content) / (10 * 1024 * 1024))))

        per_image.append(
            {
                "name": safe_name,
                "content": content,
                "meta": image_meta,
                "result": build_result(
                    filename=safe_name,
                    width=image_meta["width"],
                    height=image_meta["height"],
                    params=params,
                    scene=scene,
                ),
            }
        )

    # ---- 合并逐张结果：缺陷拼接 + 描述标注来源图名 + defect_id 全局连号 ----
    merged_defects: list[dict] = []
    for entry in per_image:
        for defect in entry["result"].get("defects", []):
            defect["description"] = f"{entry['name']}：{defect.get('description', '')}"
            merged_defects.append(defect)
    for index, defect in enumerate(merged_defects):
        defect["defect_id"] = f"D{index + 1:03d}"

    abnormal_count = sum(1 for entry in per_image if entry["result"].get("status") == "abnormal")
    if merged_defects:
        result = {
            "status": "abnormal",
            "summary": f"检测到{len(merged_defects)}处疑似缺陷（{abnormal_count}/{len(per_image)}张图异常）",
            "defects": merged_defects,
        }
    else:
        result = {
            "status": "normal",
            "summary": "未发现明显异常",
            "defects": [],
        }

    # 可选 artifact：逐张标注图
    artifacts = []

    if params.get("return_annotated"):
        for entry in per_image:
            artifact_bytes = generate_annotated_png(entry["content"], entry["result"])

            if artifact_bytes:
                base_name = os.path.splitext(entry["name"])[0] + "_annotated.png"
                uri = store_artifact(task_id, base_name, artifact_bytes)

                artifacts.append(
                    {
                        "artifact_type": "annotated_image",
                        "mime_type": "image/png",
                        "name": base_name,
                        "uri": uri,
                    }
                )

    first = per_image[0]
    elapsed_ms = int((time.perf_counter() - start_time) * 1000)

    payload = {
        "code": 0,
        "message": "success",
        "task_id": task_id,
        "trace_id": trace,
        "tool": {
            "tool_name": TOOL_NAME,
            "tool_version": TOOL_VERSION,
            "model_version": MODEL_VERSION,
            "api_version": API_VERSION,
        },
        "input": {
            "image_name": first["name"],
            "image_count": len(per_image),
            "width": first["meta"]["width"],
            "height": first["meta"]["height"],
            "engine_id": engine_id,
            "component_id": component_id,
            "image_id": image_id,
        },
        "performance": {
            "processing_time_ms": elapsed_ms,
        },
        "result": result,
        "artifacts": artifacts,
    }

    logger.info(
        "inference done, task_id=%s, trace_id=%s, status=%s, elapsed_ms=%s",
        task_id,
        trace,
        result.get("status"),
        elapsed_ms,
    )

    return JSONResponse(status_code=200, content=payload)


# =========================
# 接口：artifact 下载
# =========================

@app.get("/artifacts/{task_id}/{filename}")
async def get_artifact(task_id: str, filename: str):
    """
    下载 mock artifact。
    仅用于演示，生产环境建议由对象存储或文件服务提供。
    """
    if ".." in filename or "/" in filename or "\\" in filename:
        return make_error(
            code=1001,
            message="invalid artifact filename",
            http_status=400,
            task_id=task_id,
        )

    key = f"{task_id}/{filename}"
    data = ARTIFACTS.get(key)

    if data is None:
        return make_error(
            code=4001,
            message="artifact not found",
            http_status=404,
            task_id=task_id,
        )

    return Response(content=data, media_type="image/png")


# =========================
# 本地启动入口
# =========================

if __name__ == "__main__":
    import sys

    if "--identity" in sys.argv:
        # 供 scripts/sync_capabilities.py 子进程校验域名覆盖（不校验 tool_name：
        # 单进程多域，tool_name 仅进程名元数据）
        print(
            json.dumps(
                {
                    "tool_name": TOOL_NAME,
                    "tool_version": TOOL_VERSION,
                    "model_version": MODEL_VERSION,
                    "api_version": API_VERSION,
                    "defect_domains": DEFECT_DOMAINS,
                }
            )
        )
        sys.exit(0)

    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8002")),
        workers=1,
    )