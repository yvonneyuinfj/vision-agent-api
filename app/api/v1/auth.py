"""登录认证接口：login / logout / me。

- login：校验账号密码 → 签发 httpOnly 会话 Cookie
- logout：清除会话 Cookie
- me：返回当前登录状态（前端路由守卫据此跳转）
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core import context
from app.core.auth import AuthService
from app.core.config import get_settings
from app.core.response import StandardEnvelope

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


def _auth(request: Request) -> AuthService | None:
    """从 app.state 取 AuthService；未初始化返回 None。"""
    svc = getattr(request.app.state, "auth", None)
    return svc if isinstance(svc, AuthService) else None


def _json(response: StandardEnvelope[dict], status: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status, content=response.to_shell())


@router.post("/login")
async def login(body: LoginBody, request: Request) -> JSONResponse:
    svc = _auth(request)
    settings = get_settings()
    if not settings.auth_enabled:
        return _json(StandardEnvelope.ok({"logged_in": True, "username": body.username}))

    if svc is None or not svc.check_credentials(body.username, body.password):
        return _json(
            StandardEnvelope.err(
                code=401,
                message="用户名或密码错误",
                trace_id=context.get_trace_id(),
            ),
            status=401,
        )

    response = _json(
        StandardEnvelope.ok(
            {"logged_in": True, "username": body.username, "expires_hours": settings.auth_session_hours}
        )
    )
    svc.set_session_cookie(response)
    return response


@router.post("/logout")
async def logout(request: Request) -> JSONResponse:
    svc = _auth(request)
    response = _json(StandardEnvelope.ok({"logged_in": False}))
    if svc is not None:
        svc.clear_session_cookie(response)
    return response


@router.get("/me")
async def me(request: Request) -> StandardEnvelope[dict]:
    """当前登录态（不要求登录也可调用；前端据此决定是否展示登录页）。"""
    svc = _auth(request)
    settings = get_settings()
    if not settings.auth_enabled:
        return StandardEnvelope.ok({"logged_in": True, "username": settings.auth_username})

    username = None
    if svc is not None:
        username = svc.session_from_cookie(request.cookies.get(settings.auth_cookie_name))
    return StandardEnvelope.ok({"logged_in": bool(username), "username": username or ""})