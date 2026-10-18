"""select_capability 节点：声明 scenes 空交集可读报错、输入图片装载、多模态识别装配。

覆盖 2026-08-20 场景识别链路修复：用户声明的 scenes 列表进入路由，
图片字节随 LLM 调用发送；零交集时给出行内可读的 4004 而非静默失败。
"""
from __future__ import annotations

import pytest

from app.agents.nodes.select_capability import _load_input_images, select_capability_node
from app.core.errors import BizCode
from app.models.capability import CapabilityManifest
from app.repositories.file_repo import FileRepo
from app.services.deps import AgentContext


class _ManifestRegistry:
    def __init__(self, scenes: list[str]) -> None:
        self._scenes = scenes

    def list(self) -> list[CapabilityManifest]:
        return [
            CapabilityManifest(capability_id=f"{s}_v1", tool_name=f"{s}_v1", scene=s)
            for s in self._scenes
        ]

    def lookup(self, scene: str) -> CapabilityManifest | None:
        for m in self.list():
            if m.scene == scene:
                return m
        return None


class _SettingsStub:
    scene_confirm_enabled = False


class _SceneLLM:
    available = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], object]] = []

    async def extract_scene(self, text: str, candidates: list[str], *, images=None) -> list[dict]:
        self.calls.append((text, candidates, images))
        return [{"scene": "fracture", "source": "llm"}]


def _ctx(**overrides) -> AgentContext:
    kwargs = {
        "registry": _ManifestRegistry(["borescope", "fracture"]),
        "llm": None,
        "settings": _SettingsStub(),
        "file_repo": None,
    }
    kwargs.update(overrides)
    return AgentContext(**kwargs)


def _state(**overrides) -> dict:
    base = {
        "task_id": "T1",
        "scene": None,
        "scenes": None,
        "input_uris": ["/api/v1/tasks/T1/files/class3_1.jpg?kind=inputs"],
        "parameters": {},
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_declared_scenes_without_registered_overlap_fails_with_readable_4004():
    result = await select_capability_node(
        _state(scenes=["metallography", "fpi"], llm=None), _ctx()
    )
    assert result["error"]["node"] == "select_capability"
    assert result["error"]["code"] == int(BizCode.SCENARIO_NOT_SUPPORTED)
    # 报出用户声明了什么、注册了什么，而非笼统的“无法识别”
    assert "metallography" in result["error"]["message"]
    assert "borescope" in result["error"]["message"]


@pytest.mark.asyncio
async def test_declared_scenes_with_images_route_through_multimodal_llm(tmp_path):
    """声明 ∩ 注册表 = {borescope, fracture}：多模态 LLM 从图片判断出 fracture。"""
    repo = FileRepo(root=str(tmp_path))
    uri = repo.save_bytes("T7", "class3_1.jpg", b"\xff\xd8jpegdata", kind="inputs")
    llm = _SceneLLM()

    result = await select_capability_node(
        _state(
            task_id="T7",
            scenes=["metallography", "fracture", "fpi", "borescope"],
            input_uris=[uri],
        ),
        _ctx(file_repo=repo, llm=llm),
    )

    assert "error" not in result
    assert result["scene"] == "fracture"
    # 能力命中已下沉到 resolve_capability 节点，本节点只负责识别与候选
    assert result["selected_capability"] is None
    _, candidates, images = llm.calls[0]
    assert candidates == ["fracture", "borescope"]  # 候选收窄为声明 ∩ 注册表
    assert images == [("image/jpeg", b"\xff\xd8jpegdata")]  # 真实图片字节进 VLM


def test_load_input_images_reads_jpeg_and_skips_unsupported_or_bad_uri(tmp_path):
    repo = FileRepo(root=str(tmp_path))
    uri = repo.save_bytes("T9", "class3_1.jpg", b"\xff\xd8jpg", kind="inputs")
    repo.save_bytes("T9", "meta.csv", b"a,b\n", kind="inputs")  # 非图像后缀 → 跳过

    images = _load_input_images([uri, "/bad/uri", "/api/v1/tasks/T9/files/x.tif?kind=inputs"], _ctx(file_repo=repo))

    assert images == [("image/jpeg", b"\xff\xd8jpg")]