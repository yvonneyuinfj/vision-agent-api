"""智能质检 Agent 系统入口。创建即运行相关文件（同 FastAPI 工程选择此布局）。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import routers
from app.core.auth import AuthService
from app.core.config import get_settings
from app.core.handlers import register_exception_handlers
from app.core.logger import get_logger, setup_app_logging
from app.core.middleware import register_middlewares
from app.services.container import AppContainer

logger = get_logger("app.main")

# 前后端分离：本服务只提供 /api/v1 与 /docs；静态前端由 Nginx 托管并反代到此（见 deploy/nginx/）。
# 后端应只监听回环地址（生产经 Nginx 对外，见 .env 的 API_HOST/API_PORT）。


@asynccontextmanager
async def lifespan(app: FastAPI):
    container = AppContainer()
    await container.startup()
    app.state.container = container
    # 登录认证服务（挂在 app.state，供 v1/auth 与认证中间件使用）
    settings = get_settings()
    app.state.auth = AuthService(
        username=settings.auth_username,
        password=settings.auth_password,
        secret=settings.auth_secret,
        cookie_name=settings.auth_cookie_name,
        session_hours=settings.auth_session_hours,
    )
    yield
    await container.shutdown()


def create_app() -> FastAPI:
    setup_app_logging()

    settings = get_settings()
    app = FastAPI(
        title="智能质检 Agent 系统",
        version=settings.tool_version,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS（私有化内网部署，来源按环境配置；禁止通配 + 携带凭证）
    cors_origins = _env_list("CORS_ORIGINS", ["http://localhost:5173"])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 统一异常与响应外壳
    register_middlewares(app)
    register_exception_handlers(app)

    # 路由（严格分层：路由层仅做校验与外壳；新增路由只需加入 api/v1/__init__.py）
    for router in routers:
        app.include_router(router)

    # 前后端分离：后端只负责 /api/v1 与 /docs，静态前端与反向代理由 Nginx 承担。
    # 根路径保留一个服务探测（非 SPA 托管）。
    @app.get("/")
    async def root() -> dict[str, str]:
        return {"service": settings.tool_name, "version": settings.tool_version}

    return app


def _env_list(name: str, default: list[str]) -> list[str]:
    import os

    raw = os.getenv(name)
    if not raw:
        return default
    return [o.strip() for o in raw.split(",") if o.strip()]


app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )