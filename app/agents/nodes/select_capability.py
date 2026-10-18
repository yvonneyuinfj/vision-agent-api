"""节点：能力选择（a.md §9 select_capability，合并原 intent + route）。

场景识别经 SceneRouter（规则优先 + 用户声明 scenes + LLM 多模态兜底），
多候选歧义且开关开启时不直接 interrupt（本节点的图片读取/VLM 识别属非幂等
副作用，resume 会从头重跑），而是写 candidate_scenes 交下游 scene_confirm
节点暂停确认；识别出场景即返回，能力命中由 resolve_capability 节点完成。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.agents.nodes.node_utils import error_state, node
from app.agents.routers.scene_router import SceneRouter
from app.core.errors import BizCode
from app.core.logger import get_logger
from app.services.deps import AgentContext

logger = get_logger("app.agents.nodes.select_capability")

# 送 VLM 判场景的图片格式白名单（jpeg/png；tif 等暂不送多模态，仍走文本）
_SCENE_IMAGE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def _declared_scenes(state: dict[str, Any]) -> list[str]:
    """取用户声明的多场景列表（strings only，防御脏数据）。"""
    raw = state.get("scenes") or []
    return [s for s in raw if isinstance(s, str) and s.strip()]


def _load_input_images(input_uris: list[str], ctx: AgentContext) -> list[tuple[str, bytes]]:
    """输入原图 -> (media_type, bytes) 列表，供 VLM 多模态判场景。

    畸形 URI/读取失败/非白名单格式逐条跳过并打 warning，绝不让图片侧故障
    拖垮文本规则识别路径。
    """
    file_repo = ctx.file_repo
    if file_repo is None:
        return []
    images: list[tuple[str, bytes]] = []
    for uri in input_uris or []:
        try:
            path: Path = file_repo.uri_to_path(uri)
        except ValueError as exc:
            logger.warning("select_capability: skip unreadable input uri %s: %s", uri, exc)
            continue
        media_type = _SCENE_IMAGE_MIME.get(path.suffix.lower())
        if media_type is None:
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            logger.warning("select_capability: skip unreadable image %s: %s", path.name, exc)
            continue
        if data:
            images.append((media_type, data))
    return images


@node("select_capability")
async def select_capability_node(state: dict[str, Any], ctx: AgentContext) -> dict[str, Any]:
    registry = ctx.registry
    if registry is None:
        return error_state("select_capability", BizCode.INTERNAL_ERROR, "registry unavailable")

    registered = [m.scene for m in registry.list()]
    declared = _declared_scenes(state)
    if declared and not any(s in registered for s in declared):
        # 声明 scenes 与注册表零交集：可读报错（列出双方），不静默收敛为“无法识别”
        return error_state(
            "select_capability",
            BizCode.SCENARIO_NOT_SUPPORTED,
            f"声明的场景 {sorted(set(declared))} 均未注册（已注册：{', '.join(sorted(registered)) or '无'}）",
        )

    images = await asyncio.to_thread(_load_input_images, state.get("input_uris") or [], ctx)
    router = SceneRouter(llm=ctx.llm, scene_names=registered)
    scene, candidates = await router.recognize(
        scene=state.get("scene") or None,
        parameters=state.get("parameters") or {},
        input_uris=state.get("input_uris") or [],
        scenes=declared or None,
        images=images or None,
    )
    # 多候选歧义 + 开关开启：不在本节点 interrupt（上方图片读取/VLM 识别在
    # resume 时会从头重跑，既重复计费又可能因 LLM 非确定性改变候选集合），
    # 交由下游 scene_confirm 节点暂停确认；候选在此一次性写入 state。
    need_confirm = (
        scene is None
        and getattr(ctx.settings, "scene_confirm_enabled", False)
        and len(candidates) > 1
    )
    if scene is None and not need_confirm:
        return error_state("select_capability", BizCode.SCENARIO_NOT_SUPPORTED, "无法识别检测场景")

    # scene 为 None（待 scene_confirm 恢复确认）时 selected_capability 置 None，
    # 由下游 resolve_capability 节点在 scene 确定后完成能力命中。
    return {
        "scene": scene,
        "candidate_scenes": candidates,
        "selected_capability": None,
    }
