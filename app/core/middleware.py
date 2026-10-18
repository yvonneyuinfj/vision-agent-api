"""HTTP 中间件：trace_id 注入响应头 + 兜底非 JSON 错误响应转外壳。

兜底逻辑与 mock_service 一致（规范 §7：失败时不得返回纯文本/HTML 错误页）。
"""
from __future__ import annotations

from app.core.logger import get_logger

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core import context
from app.core.errors import BizCode, http_status_of
from app.core.response import StandardEnvelope

logger = get_logger("app.http.middleware")

_TRACE_HEADER = "X-Trace-Id"


class TraceMiddleware(BaseHTTPMiddleware):
    """每次请求：解析/生成 trace_id 写入 contextvars，回写响应头。"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        trace_id = request.headers.get(_TRACE_HEADER) or context.new_trace_id()
        context.set_trace_id(trace_id)
        context.set_task_id(None)

        response = await call_next(request)
        response.headers[_TRACE_HEADER] = trace_id
        return response


class JsonFallbackMiddleware(BaseHTTPMiddleware):
    """未被路由/异常处理器覆盖的 4xx/5xx（如 404/405 直出）统一转外壳 JSON。"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)

        content_type = response.headers.get("content-type", "")
        if response.status_code < 400 or content_type.startswith("application/json"):
            return response

        body = b"".join([chunk async for chunk in response.body_iterator])
        message = body.decode("utf-8", "replace").strip() or "request failed"
        logger.warning(
            "fallback json, path=%s, status=%s, body=%s",
            request.url.path,
            response.status_code,
            message[:200],
        )
        return JSONResponse(
            status_code=response.status_code,
            content=StandardEnvelope.err(
                code=BizCode.INTERNAL_ERROR,
                message=message,
                trace_id=context.get_trace_id(),  # 状态未映射时兜底 4001
            ).model_dump(),
        )


def register_middlewares(app: FastAPI) -> None:
    app.add_middleware(JsonFallbackMiddleware)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(TraceMiddleware)


# ---------------- 登录认证中间件 ----------------

# 不需要登录校验的 API 前缀（登录路由自身 + 健康检查，供外部监控探活）
_AUTH_EXEMPT_PREFIXES = ("/api/v1/auth", "/api/v1/health", "/docs", "/redoc", "/openapi.json")


class AuthMiddleware(BaseHTTPMiddleware):
    """保护业务 API：未登录访问 /api/v1/* 返回 401（前端跳登录页）。

    - 仅拦截 /api/v1/* 业务接口；前端 SPA 静态资源与登录页不在此列（cors 不需拦截）
    - 豁免 auth 路由自身与健康检查
    - auth_enabled=False 时完全放行（保持旧行为）
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        # 仅保护业务 API
        if not path.startswith("/api/v1/"):
            return await call_next(request)

        # 豁免登录/健康
        if any(path == p or path.startswith(p + "/") for p in _AUTH_EXEMPT_PREFIXES):
            return await call_next(request)

        # auth_enabled=False 完全放行
        from app.core.config import get_settings

        settings = get_settings()
        if not settings.auth_enabled:
            return await call_next(request)

        svc = getattr(request.app.state, "auth", None)
        if svc is None:
            # 服务未初始化（理论不发生）也拒绝，避免绕过
            return JSONResponse(
                status_code=401,
                content=StandardEnvelope.err(
                    code=401,
                    message="未登录或会话已失效",
                    trace_id=context.get_trace_id(),
                ).to_shell(),
            )

        cookie_value = request.cookies.get(settings.auth_cookie_name)
        if svc.session_from_cookie(cookie_value):
            return await call_next(request)

        logger.info("auth reject, path=%s, trace=%s", path, context.get_trace_id())
        return JSONResponse(
            status_code=401,
            content=StandardEnvelope.err(
                code=401,
                message="未登录或会话已失效",
                trace_id=context.get_trace_id(),
            ).to_shell(),
        )