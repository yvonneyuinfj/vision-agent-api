"""场景目录（决策 52）：scene 路由键的**唯一事实源**。

批次 3 之前"六"这个数字卡在四处：写侧白名单、自动识别关键词表、VLM 六选一提示词、
前端 `WORKBENCHES`。本目录只取代第 1 与第 4 处的权威——**关键词表与提示词仍只认六键**，
所以目录里 `auto_recognize=False` 的场景必须由人工在工作台/提交页显式选场景，
登记一个新 scene 不会让它被自动路由命中。这一限制是用户可见的，随 `/scenes` 下发。
"""
from __future__ import annotations

from app.agents.routers.scene_router import SCENE_KEYWORDS
from app.models.scene import SceneSpec

# 顺序即侧栏与首页卡片的呈现顺序（与前端 constants/workbench.ts 的兜底表同序同值）。
SCENE_CATALOG: dict[str, SceneSpec] = {
    spec.scene: spec
    for spec in (
        SceneSpec(
            scene="fpi",
            label="荧光渗透检测",
            short="荧光",
            en="FPI",
            params="frozen",
            input="仅需检测图像 · 暗场",
            auto_recognize="fpi" in SCENE_KEYWORDS,
        ),
        SceneSpec(
            scene="borescope",
            label="孔探检测",
            short="孔探",
            en="BORESCOPE",
            params="frozen",
            input="图像 / 录像均可",
            auto_recognize="borescope" in SCENE_KEYWORDS,
        ),
        SceneSpec(
            scene="infrared",
            label="热部图像检测",
            short="红外",
            en="INFRARED",
            params="draft",
            input="图像 + 辐射值 CSV",
            auto_recognize="infrared" in SCENE_KEYWORDS,
        ),
        SceneSpec(
            scene="dr",
            label="X射线检测",
            short="X射线",
            en="DR",
            params="draft",
            input="图像 + 参考图 + 标定 JSON/XML",
            auto_recognize="dr" in SCENE_KEYWORDS,
        ),
        SceneSpec(
            scene="fracture",
            label="断口分析",
            short="断口",
            en="FRACTURE",
            params="frozen",
            input="仅需检测图像",
            auto_recognize="fracture" in SCENE_KEYWORDS,
        ),
        SceneSpec(
            scene="metallography",
            label="金相分析",
            short="金相",
            en="METALLOGRAPHY",
            params="frozen",
            input="仅需检测图像 · 附放大倍数",
            auto_recognize="metallography" in SCENE_KEYWORDS,
        ),
    )
}


def scene_specs() -> list[SceneSpec]:
    return list(SCENE_CATALOG.values())
