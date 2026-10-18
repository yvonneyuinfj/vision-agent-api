"""场景目录路由（决策 52）：只读，供前端把工作台/提交页/注册表单的场景项改为取数。

没有写入端——场景的新增是**代码变更**（进 `app/services/scene_catalog.py`），
不是运营可自助登记的配置项；本轮如此收缩是因为自动识别关键词表与 VLM 提示词仍写死六键，
放开写入只会造出界面可达、任务永不被自动路由的假场景。
"""
from __future__ import annotations

from fastapi import APIRouter

from app.core.response import PageMeta, PageResult, StandardEnvelope
from app.models.scene import SceneSpec
from app.services.scene_catalog import scene_specs

router = APIRouter(prefix="/api/v1/scenes", tags=["platform"])


@router.get("", response_model=StandardEnvelope[PageResult[SceneSpec]])
async def list_scenes() -> StandardEnvelope[PageResult[SceneSpec]]:
    items = scene_specs()
    return StandardEnvelope.ok(
        PageResult(items=items, meta=PageMeta(total=len(items), page=1, page_size=len(items)))
    )
