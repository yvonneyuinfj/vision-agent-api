"""api/v1 平铺路由集合（新增路由只需加入此列表，main 零改动）。"""
from app.api.v1 import (
    agent_session,
    auth,
    capability,
    document,
    health,
    ragflow,
    report,
    scene,
    task,
)

routers = [
    auth.router,
    health.router,
    capability.router,
    scene.router,
    task.router,
    document.router,
    report.router,
    ragflow.router,
    agent_session.router,
]
