"""业务错误码（对齐《动力所工业图像智能算法工具统一API 接入规范》§8）+ 平台扩展。

HTTP 状态码与业务码的映射集中维护，异常处理中间件据此归一化。
"""
from __future__ import annotations

from enum import IntEnum


class BizCode(IntEnum):
    SUCCESS = 0
    # ---- 规范 §8 定义 ----
    INVALID_REQUEST = 1001          # 400 缺少必填字段或参数格式错误
    UNSUPPORTED_IMAGE_FORMAT = 1002 # 415
    IMAGE_TOO_LARGE = 1003          # 413
    INVALID_IMAGE = 1004            # 422
    INVALID_PARAMETER = 2001        # 422 参数越界或组合不合法
    MODEL_NOT_READY = 3001          # 503 模型/能力未就绪
    INFERENCE_FAILED = 3002         # 500
    INFERENCE_TIMEOUT = 3003        # 504
    INTERNAL_ERROR = 4001           # 500 未分类内部错误
    # ---- 平台层扩展（agent 平台自身的业务语义）----
    TASK_NOT_FOUND = 4002           # 404 任务不存在
    TASK_STATE_CONFLICT = 4003      # 409 任务状态不允许该操作（如重复复核）
    SCENARIO_NOT_SUPPORTED = 4004   # 404 场景无可用能力（路由未命中）
    CAPABILITY_NOT_READY = 3001     # 复用 3001
    RAG_UNAVAILABLE = 4005          # 503 知识库不可用
    LLM_UNAVAILABLE = 4006          # 503 大模型服务不可用
    ADAPTER_ERROR = 4007            # 502 算法网关适配失败


# 业务码 -> HTTP 状态（异常处理器只认 HTTP 状态 + 业务码透传）
BIZ_HTTP_STATUS: dict[BizCode, int] = {
    BizCode.SUCCESS: 200,
    BizCode.INVALID_REQUEST: 400,
    BizCode.UNSUPPORTED_IMAGE_FORMAT: 415,
    BizCode.IMAGE_TOO_LARGE: 413,
    BizCode.INVALID_IMAGE: 422,
    BizCode.INVALID_PARAMETER: 422,
    BizCode.MODEL_NOT_READY: 503,
    BizCode.INFERENCE_FAILED: 500,
    BizCode.INFERENCE_TIMEOUT: 504,
    BizCode.INTERNAL_ERROR: 500,
    BizCode.TASK_NOT_FOUND: 404,
    BizCode.TASK_STATE_CONFLICT: 409,
    BizCode.SCENARIO_NOT_SUPPORTED: 404,
    BizCode.RAG_UNAVAILABLE: 503,
    BizCode.LLM_UNAVAILABLE: 503,
    BizCode.ADAPTER_ERROR: 502,
}


class BusinessError(Exception):
    """平台自定义业务异常：携带业务码与 HTTP 状态，由统一异常处理器转外壳。"""

    def __init__(
        self,
        code: BizCode,
        message: str,
        *,
        http_status: int | None = None,
        task_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status or BIZ_HTTP_STATUS.get(code, 500)
        self.task_id = task_id
        self.trace_id = trace_id


def http_status_of(code: BizCode) -> int:
    return BIZ_HTTP_STATUS.get(code, 500)