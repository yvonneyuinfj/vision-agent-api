"""FastAPI 统一异常处理：业务异常 / 校验失败 / 未分类异常 -> 规范外壳。

与 mock_service/main.py 同一策略，但面向平台自身：
- RequestValidationError -> 1001 INVALID_REQUEST
- BusinessError          -> 透传业务码 + http_status
- StarletteHTTPException -> HTTP 状态映射业务码（规范 §8 表）
- 兜底 Exception         -> 4001 INTERNAL_ERROR（生产不泄漏堆栈）
"""
from __future__ import annotations

from app.core.logger import get_logger

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core import context
from app.core.errors import BizCode, BusinessError
from app.core.response import StandardEnvelope

logger = get_logger("app.http.errors")


def _err_payload(
    code: BizCode,
    message: str,
    *,
    task_id: str | None = None,
) -> StandardEnvelope[None]:
    return StandardEnvelope.err(
        code=code,
        message=message,
        task_id=task_id or context.get_task_id(),
        trace_id=context.get_trace_id(),
    )


def _validation_message(exc: RequestValidationError) -> str:
    msgs: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", []))
        msgs.append(f"{loc}: {err.get('msg', 'invalid')}")
    return "; ".join(msgs) or "invalid request"


def _http_to_biz(status: int) -> BizCode:
    """HTTP 状态 -> 规范业务码（§8）；未覆盖状态归 4001。"""
    table: dict[int, BizCode] = {
        400: BizCode.INVALID_REQUEST,
        413: BizCode.IMAGE_TOO_LARGE,
        415: BizCode.UNSUPPORTED_IMAGE_FORMAT,
        422: BizCode.INVALID_PARAMETER,
        503: BizCode.MODEL_NOT_READY,
        504: BizCode.INFERENCE_TIMEOUT,
    }
    return table.get(status, BizCode.INTERNAL_ERROR)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        code = BizCode.INVALID_REQUEST
        message = _validation_message(exc)
        logger.warning("validation error, path=%s, detail=%s", request.url.path, message)
        return JSONResponse(
            status_code=400,
            content=_err_payload(code, message).model_dump(),
        )

    @app.exception_handler(BusinessError)
    async def _business(request: Request, exc: BusinessError) -> JSONResponse:
        logger.warning(
            "business error, path=%s, code=%s, message=%s, task_id=%s",
            request.url.path,
            exc.code,
            exc.message,
            exc.task_id,
        )
        return JSONResponse(
            status_code=exc.http_status,
            content=_err_payload(exc.code, exc.message, task_id=exc.task_id)
            .model_dump(),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _http_to_biz(exc.status_code)
        message = str(exc.detail)
        if exc.status_code == 404:
            code = BizCode.SCENARIO_NOT_SUPPORTED if _is_capability_path(request) else BizCode.TASK_NOT_FOUND
        logger.warning("http error, path=%s, status=%s", request.url.path, exc.status_code)
        return JSONResponse(
            status_code=exc.status_code,
            content=_err_payload(code, message).model_dump(),
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled exception, path=%s, error=%r", request.url.path, exc
        )
        return JSONResponse(
            status_code=500,
            content=_err_payload(
                BizCode.INTERNAL_ERROR, "internal error"
            ).model_dump(),
        )


def _is_capability_path(request: Request) -> bool:
    return request.url.path.startswith("/api/v1/capabilities")