"""场景识别路由（规则路由优先、LLM 辅助理解兜底）。

从原 intent 节点抽出的独立可测单元：规则关键词表命中优先；
未命中且注入 llm 可用时请求候选——单候选直接采用参与路由，
多候选视为歧义仅记录（避免误选检测工具，任务以 4004 收敛）。
"""
from __future__ import annotations

import re
from typing import Any

# 场景关键词（文件名/参数触发），与能力注册表六类场景对齐；
# yingguang/hongwai/kongtan 为客户图片拼音命名约定，规则层直采免烧 VLM
# （hongwai.png 实测为灰度图，VLM 按决策树会误判 X射线，故命名约定必须优先）
# 热部/热端 为 infrared 的业务口径（工作台「热部图像检测」/热端部件），同样进规则层
SCENE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "borescope": ("borescope", "boroscope", "内窥", "孔探", "kongtan", "crack", "burn", "corrosion"),
    "infrared": ("infrared", "thermal", "ir_", "红外", "热图", "热部", "热端", "radiance", "hongwai"),
    "dr": ("dr_", "xray", "x_ray", "dr", "射线", "dr_imaging", "xd"),
    "fpi": ("fpi", "fluorescent", "渗透", "荧光", "uv", "yingguang"),
    "fracture": ("fracture", "断口", "电镜", "scanning"),
    "metallography": ("metallography", "金相", "组织", "grain", "micro"),
}


def _match_by_keywords(texts: list[str]) -> str | None:
    joined = " ".join(texts).lower()
    for scene, keywords in SCENE_KEYWORDS.items():
        if any(kw.lower() in joined for kw in keywords):
            return scene
    return None


# 前端能力下拉「指定方向」拼接的文字标注（use-detection buildContent），
# 会话轨路由层将其解析为显式场景信号（灰度热部图会被 VLM 决策树误判 X射线）
_EXPLICIT_DIRECTION_RE = re.compile(r"[（(]\s*指定方向[:：]\s*([^（()）]{1,32}?)\s*[）)]")


def parse_explicit_direction(text: str) -> str | None:
    """从「（指定方向：xxx）」标注中提取能力名并按关键词表归场景。

    仅匹配标注内的文字（避免正文误触发）；无标注或标注内容对不上
    任何场景时返回 None，交回上层走常规路由。
    """
    if not text:
        return None
    m = _EXPLICIT_DIRECTION_RE.search(text)
    if not m:
        return None
    return _match_by_keywords([m.group(1)])


class SceneRouter:
    """场景识别：规则优先，用户声明 scenes 收窄候选，LLM 多模态兜底。"""

    def __init__(self, llm: Any, scene_names: list[str]) -> None:
        self._llm = llm
        self._scene_names = scene_names

    async def recognize(
        self,
        *,
        scene: str | None,
        parameters: dict[str, Any],
        input_uris: list[str],
        scenes: list[str] | None = None,
        images: list[tuple[str, bytes]] | None = None,
    ) -> tuple[str | None, list[dict[str, Any]]]:
        # ---- 规则优先：文件名 / 参数文本匹配 ----
        resolved = scene or None
        texts: list[str] = []
        if resolved is None:
            texts = [uri.split("/")[-1] for uri in input_uris]
            texts += [
                str(v) for v in parameters.values() if not isinstance(v, (list, dict))
            ]
            resolved = _match_by_keywords(texts)

        # ---- 用户声明 scenes：与注册表求交收窄候选 ----
        declared = [s for s in (scenes or []) if s in self._scene_names]

        # ---- 规则不命中且 LLM 可用：辅助识别候选（可多模态看图） ----
        candidates: list[dict[str, Any]] = []
        if resolved is None:
            if scenes and not declared:
                # 声明与注册表零交集：不兜底全表候选（避免误选其他检测工具），
                # 交上层给出可读错误（详见 select_capability 节点）。
                return None, []
            if len(declared) == 1:
                # 声明恰含一个已注册场景：确定性采用，不烧 LLM。
                resolved = declared[0]
                candidates = [{"scene": resolved, "source": "declared"}]
            elif self._llm is not None and getattr(self._llm, "available", False):
                try:
                    pool = declared or self._scene_names
                    # 新版返回富候选（含置信度/证据/推理链），直通下游
                    # scene_confirm 载荷，供人工确认时参考
                    candidates = await self._llm.extract_scene(
                        " ".join(texts) or "（无文件名与参数文本）",
                        pool,
                        images=images,
                    )
                except Exception:  # noqa: BLE001 - LLM 失败降级为规则结果
                    pass
        # 单候选直接采用；多候选歧义仅记录不路由
        if resolved is None and len(candidates) == 1:
            resolved = candidates[0]["scene"]
        return resolved, candidates
