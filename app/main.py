"""智能质检 Agent 系统入口。创建即运行相关文件（同 FastAPI 工程选择此布局）。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api.v1 import routers
from app.core.auth import AuthService
from app.core.config import get_settings
from app.core.handlers import register_exception_handlers
from app.core.logger import get_logger, setup_app_logging
from app.core.middleware import register_middlewares
from app.services.container import AppContainer

logger = get_logger("app.main")

# 前端构建产物目录（frontend/dist）；不存在时退回根路径探测
FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"


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

    # 前端静态托管（注册在全部 API/docs 路由之后，catch-all 仅在无更早匹配时生效）
    if (FRONTEND_DIST / "index.html").is_file():
        _register_frontend(app)
    else:
        logger.warning("前端构建产物不存在，跳过静态托管: %s", FRONTEND_DIST)

        # 根路径探测（仅未构建前端时保留）
        @app.get("/")
        async def root() -> dict[str, str]:
            return {"service": settings.tool_name, "version": settings.tool_version}

    return app


def _register_frontend(app: FastAPI) -> None:
    """托管 frontend/dist：真实存在的静态文件直出；其余无扩展名路径回落 index.html（SPA 路由）。"""
    index_file = FRONTEND_DIST / "index.html"
    dist_root = FRONTEND_DIST.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def frontend(full_path: str) -> FileResponse:
        if full_path:
            target = (dist_root / full_path).resolve()
            # 防路径穿越：只允许命中 dist 目录内的真实文件
            if target.is_file() and target.is_relative_to(dist_root):
                return FileResponse(target)
            # 带扩展名的资源未命中 → 404 外壳，避免把 index.html 当 JS/CSS 返回
            if "." in Path(full_path).name:
                raise HTTPException(status_code=404, detail="static asset not found")
        return FileResponse(index_file)


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