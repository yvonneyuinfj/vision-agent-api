"""SceneRouter 场景识别测试（a.md §11 规则路由优先、LLM 兜底）。"""
from __future__ import annotations

import pytest


class _FakeLLM:
    def __init__(self, scenes: list[str]) -> None:
        self.scenes = scenes
        self.available = True
        self.calls: list[tuple[str, list[str], object]] = []

    async def extract_scene(self, text: str, candidates: list[str], *, images=None) -> list[dict]:
        self.calls.append((text, candidates, images))
        # 新契约：返回富候选（scene + source），SceneRouter 直通下游
        return [{"scene": s, "source": "llm"} for s in self.scenes]


class _NoLLM:
    available = False


@pytest.mark.asyncio
async def test_keyword_rule_wins_without_llm():
    from app.agents.routers.scene_router import SceneRouter

    router = SceneRouter(llm=_NoLLM(), scene_names=["borescope", "fracture"])
    scene, candidates = await router.recognize(
        scene=None,
        parameters={},
        input_uris=["uploads/IMG_0042_borescope.jpg"],
    )
    assert scene == "borescope"
    assert candidates == []


@pytest.mark.asyncio
async def test_parameters_keywords_match():
    from app.agents.routers.scene_router import SceneRouter

    router = SceneRouter(llm=_NoLLM(), scene_names=["infrared", "dr"])
    scene, _ = await router.recognize(
        scene=None,
        parameters={"radiance_csv": "IR_scan_001.csv"},
        input_uris=["uploads/x.jpg"],
    )
    assert scene == "infrared"


@pytest.mark.asyncio
async def test_llm_single_candidate_adopted_when_rule_misses():
    """规则未命中且 LLM 恰好返回单候选：直接采用该场景参与路由。"""
    from app.agents.routers.scene_router import SceneRouter

    router = SceneRouter(llm=_FakeLLM(["fpi"]), scene_names=["fpi", "metallography"])
    scene, candidates = await router.recognize(
        scene=None,
        parameters={},
        input_uris=["uploads/IMG_1001.jpg"],
    )
    assert scene == "fpi"
    assert candidates == [{"scene": "fpi", "source": "llm"}]


@pytest.mark.asyncio
async def test_llm_multiple_candidates_stay_unresolved():
    """多候选视为歧义：仅记录候选，不路由（避免误选检测工具）。"""
    from app.agents.routers.scene_router import SceneRouter

    router = SceneRouter(
        llm=_FakeLLM(["fpi", "metallography"]), scene_names=["fpi", "metallography"]
    )
    scene, candidates = await router.recognize(
        scene=None,
        parameters={},
        input_uris=["uploads/IMG_1001.jpg"],
    )
    assert scene is None
    assert candidates == [
        {"scene": "fpi", "source": "llm"},
        {"scene": "metallography", "source": "llm"},
    ]


@pytest.mark.asyncio
async def test_no_llm_and_no_rule_returns_none():
    from app.agents.routers.scene_router import SceneRouter

    router = SceneRouter(llm=_NoLLM(), scene_names=["borescope"])
    scene, candidates = await router.recognize(
        scene=None,
        parameters={},
        input_uris=["uploads/IMG_1001.jpg"],
    )
    assert scene is None
    assert candidates == []


# ---------------------------------------------------------------------------
# 用户声明 scenes 列表 + 图片字节（多模态）行为
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_declared_scenes_narrow_llm_pool_and_forward_images():
    """声明 scenes 与注册表求交后收窄候选，图片字节原样转发给 LLM。"""
    from app.agents.routers.scene_router import SceneRouter

    images = [("image/jpeg", b"\xff\xd8\xff\xe0fake")]
    llm = _FakeLLM(["fracture"])
    router = SceneRouter(llm=llm, scene_names=["borescope", "fracture"])
    scene, candidates = await router.recognize(
        scene=None,
        parameters={},
        input_uris=["uploads/class3_1.jpg", "uploads/class3_2.jpg", "uploads/class3_3.jpg"],
        scenes=["metallography", "fracture", "fpi", "borescope"],
        images=images,
    )
    assert scene == "fracture"
    assert candidates == [{"scene": "fracture", "source": "llm"}]
    # 候选收窄为 声明 ∩ 注册表（保序），图片字节原样到达
    assert llm.calls[0][1] == ["fracture", "borescope"]
    assert llm.calls[0][2] == images


@pytest.mark.asyncio
async def test_declared_scenes_single_overlap_adopts_deterministically_without_llm():
    """声明 scenes 恰好一个已注册场景：确定性采用，不烧 LLM（class3 无信号文件名也成立）。"""
    from app.agents.routers.scene_router import SceneRouter

    llm = _FakeLLM(["borescope"])
    router = SceneRouter(llm=llm, scene_names=["borescope", "fracture"])
    scene, candidates = await router.recognize(
        scene=None,
        parameters={},
        input_uris=["uploads/class3_1.jpg", "uploads/class3_2.jpg"],
        scenes=["metallography", "fpi", "fracture"],
    )
    assert scene == "fracture"
    assert candidates == [{"scene": "fracture", "source": "declared"}]
    assert llm.calls == []


@pytest.mark.asyncio
async def test_declared_scenes_without_registered_overlap_skips_llm_and_stays_unresolved():
    """声明 scenes 与注册表零交集：不兜底全表候选（避免误选），交上层给出可读错误。"""
    from app.agents.routers.scene_router import SceneRouter

    llm = _FakeLLM(["borescope"])
    router = SceneRouter(llm=llm, scene_names=["borescope", "fracture"])
    scene, candidates = await router.recognize(
        scene=None,
        parameters={},
        input_uris=["uploads/class3_1.jpg"],
        scenes=["metallography", "fpi"],
    )
    assert scene is None
    assert candidates == []
    assert llm.calls == []


@pytest.mark.asyncio
async def test_keyword_rule_beats_declared_scenes():
    """既有语义不变：文件名关键词规则仍优先于声明的 scenes 列表。"""
    from app.agents.routers.scene_router import SceneRouter

    router = SceneRouter(llm=_NoLLM(), scene_names=["borescope", "fracture"])
    scene, _ = await router.recognize(
        scene=None,
        parameters={},
        input_uris=["uploads/crack_region_01.jpg"],
        scenes=["fracture"],
    )
    assert scene == "borescope"


@pytest.mark.asyncio
async def test_explicit_scene_param_beats_declared_scenes():
    from app.agents.routers.scene_router import SceneRouter

    router = SceneRouter(llm=_NoLLM(), scene_names=["borescope", "fracture"])
    scene, candidates = await router.recognize(
        scene="fracture",
        parameters={},
        input_uris=[],
        scenes=["borescope"],
    )
    assert scene == "fracture"
    assert candidates == []


@pytest.mark.asyncio
async def test_customer_pinyin_naming_convention_routes_without_llm():
    """客户拼音命名约定规则直采：不烧 VLM。

    hongwai.png 实测为灰度图，若落到 VLM 会按其决策树误判为 X射线；
    命名约定必须在规则层优先命中（与 yingguang->fpi 同理）。
    """
    from app.agents.routers.scene_router import SceneRouter

    llm = _FakeLLM(["dr"])  # 若误入 LLM 会返回 dr，断言未发生
    router = SceneRouter(llm=llm, scene_names=["borescope", "infrared", "fpi", "dr"])

    for uri, expected in (
        ("uploads/hongwai.png", "infrared"),
        ("uploads/kongtan.jpg", "borescope"),
        ("uploads/yingguang_yolox_test.jpg", "fpi"),
    ):
        scene, _ = await router.recognize(scene=None, parameters={}, input_uris=[uri])
        assert scene == expected, uri
    assert llm.calls == []


# ---------------------------------------------------------------------------
# 业务口径关键词 + 「（指定方向：xxx）」显式标注解析（会话轨路由复用）
# ---------------------------------------------------------------------------


def test_hot_terms_keywords_route_to_infrared():
    """热部/热端为 infrared 的业务口径，规则层优先于 VLM（灰度图会误判 X射线）。"""
    from app.agents.routers.scene_router import _match_by_keywords

    assert _match_by_keywords(["分析热部试验图像中的异常情况"]) == "infrared"
    assert _match_by_keywords(["热端部件试验图.png"]) == "infrared"


def test_parse_explicit_direction_maps_capability_names():
    from app.agents.routers.scene_router import parse_explicit_direction

    assert parse_explicit_direction("（指定方向：热部图像）分析异常情况") == "infrared"
    assert parse_explicit_direction("（指定方向：X 射线）分析位移") == "dr"
    assert parse_explicit_direction("（指定方向：荧光渗透）检测缺陷") == "fpi"
    assert parse_explicit_direction("（指定方向：孔探）对孔探图损伤定位") == "borescope"
    # 无标注/标注对不上任何场景/空文本 → None（交回常规路由）
    assert parse_explicit_direction("正文里提到热部图像但没有标注") is None
    assert parse_explicit_direction("（指定方向：不存在的名字）正文") is None
    assert parse_explicit_direction("") is None
