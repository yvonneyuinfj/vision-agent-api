"""平台健康检查。"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request

from app.core.response import StandardEnvelope

router = APIRouter(prefix="/api/v1", tags=["platform"])

_BEIJING = ZoneInfo("Asia/Shanghai")


@router.get("/health")
async def platform_health(request: Request) -> StandardEnvelope[dict]:
    """平台存活 + 数据库可达性 + 模型配置摘要。

    全部属性走 getattr 防御：允许单文件热替换到旧版 container 上，缺属性时降级为 None 而非 500。
    """
    c = getattr(request.app.state, "container", None)
    repo = getattr(c, "task_repo", None) if c is not None else None
    db_ok = False
    db_product: str | None = None
    db_version: str | None = None
    if repo is not None:
        try:
            await repo.ping()
            db_ok = True
        except Exception:
            db_ok = False
        # 选型/版本探测仅在连通时进行，失败不阻断健康读数
        if db_ok:
            try:
                db_product, db_version = await repo.server_info()
            except Exception:
                pass

    now = datetime.now(_BEIJING)
    llm = getattr(c, "llm", None) if c is not None else None
    s = getattr(c, "settings", None) if c is not None else None
    return StandardEnvelope.ok(
        {
            "status": "healthy" if db_ok else "degraded",
            "database": "ok" if db_ok else "error",
            "server_date": now.strftime("%Y-%m-%d"),
            "server_datetime": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
            # VLM 可用性：前端据此置灰 agent 轨入口（决策 16）
            "vlm_available": bool(getattr(llm, "vlm_available", False)) if llm is not None else False,
            # 基模配置摘要（供监控页展示，不含密钥）
            "llm_model": (getattr(s, "llm_model", None) or None) if s is not None else None,
            "vlm_model": (getattr(s, "vlm_model", None) or None) if s is not None else None,
            "llm_provider": (getattr(s, "llm_provider", None) or None) if s is not None else None,
            # 平台与数据库元信息（选型/版本，监控页展示用）
            "platform_version": (getattr(s, "tool_version", None) or None) if s is not None else None,
            "database_product": db_product,
            "database_version": db_version,
        }
    )