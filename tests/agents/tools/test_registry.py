"""Tool 注册中心测试：注册 / 场景路由 / 便捷执行 / 未注册与异常收敛。"""
from __future__ import annotations

import asyncio

import pytest

from app.agents.tools.registry import Tool, ToolRegistry, ToolResult


class _FakeBorescopeTool(Tool):
    name = "detect/borescope"
    description = "孔探检测"
    scene = "borescope"

    async def execute(self, inputs):
        return {"defects": [1, 2], "level": "high"}


@pytest.fixture
def registry():
    return ToolRegistry()


def test_register_and_resolve(registry):
    registry.register(_FakeBorescopeTool())
    assert registry.has("detect/borescope")
    assert registry.get("detect/borescope") is not None
    assert registry.names() == ["detect/borescope"]


def test_register_forbids_empty_name(registry):
    class _NoNameTool(Tool):
        name = ""

        async def execute(self, inputs):
            return {}

    with pytest.raises(ValueError):
        registry.register(_NoNameTool())


def test_for_scene_filters_by_tag(registry):
    registry.register(_FakeBorescopeTool())

    @registry.register_decorator()
    async def _probe(inputs):
        return {"ok": True}

    # 全局工具（scene=""）与场景工具都命中
    names = [t.name for t in registry.for_scene("borescope")]
    assert "detect/borescope" in names and "_probe" in names
    # 其他场景只看到全局工具
    assert [t.name for t in registry.for_scene("infrared")] == ["_probe"]


@pytest.mark.asyncio
async def test_execute_success_returns_tool_result(registry):
    registry.register(_FakeBorescopeTool())
    result = await registry.execute("detect/borescope", {"x": 1})
    assert isinstance(result, ToolResult)
    assert result.ok
    assert result.result == {"defects": [1, 2], "level": "high"}


@pytest.mark.asyncio
async def test_execute_unknown_tool_collected_as_failure(registry):
    result = await registry.execute("detect/nope", {})
    assert not result.ok
    assert result.code == 404


@pytest.mark.asyncio
async def test_execute_exception_collected_as_failure(registry):
    class _BoomTool(Tool):
        name = "boom"
        scene = "x"

        async def execute(self, inputs):
            raise RuntimeError("kaboom")

    registry.register(_BoomTool())
    result = await registry.execute("boom", {})
    assert not result.ok
    assert result.code == 4001
    assert "kaboom" in (result.message or "")


def test_register_callable_wraps_fn(registry):
    async def _fn(inputs):
        return {"n": inputs.get("n", 0) + 1}

    registry.register_callable(_fn, name="utility/fn")
    assert registry.has("utility/fn")
    assert asyncio.run(registry.execute("utility/fn", {"n": 1})).result == {"n": 2}


def test_remove(registry):
    registry.register(_FakeBorescopeTool())
    registry.remove("detect/borescope")
    assert not registry.has("detect/borescope")


def test_register_rejects_non_tool(registry):
    with pytest.raises(TypeError):
        registry.register(object())  # type: ignore[arg-type]