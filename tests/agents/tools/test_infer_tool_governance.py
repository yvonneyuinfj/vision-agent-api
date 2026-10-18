"""InferTool 治理接线回归：熔断/信号量生效 + 业务码保真（网关错误码透传契约）。

对应改造：
- AdapterPool（per-tool 信号量+熔断器）经 assemble_tools 按配置装配注入 InferTool；
- 适配器抛出的 BusinessError 不再压平为 3002，业务码与消息原样保留。
"""
from __future__ import annotations

import pytest

from app.agents.tools.assemble import assemble_tools, _build_pool
from app.agents.tools.infer_tool import InferTool
from app.capabilities.adapters.base import (
    AdapterContext,
    AdapterPool,
    RawToolResponse,
    ToolAdapter,
)
from app.core.errors import BizCode, BusinessError
from app.models.capability import CapabilityManifest


def _manifest() -> CapabilityManifest:
    return CapabilityManifest(
        capability_id="cap_v1",
        tool_name="gov_tool",
        scene="gov_scene",
        endpoint="http://127.0.0.1:9",
        adapter_type="mock",
    )


class _StubAdapter:
    """可编程适配器：按脚本返回成功/抛业务错/抛未知异常。"""

    tool_name = "gov_tool"
    scene = "gov_scene"

    def __init__(self, script: list) -> None:
        self._script = list(script)
        self.calls = 0

    async def health(self) -> tuple[bool, str]:
        return True, "ok"

    async def invoke(self, ctx, image_paths, params, *, auxiliary=None):  # noqa: ANN001
        self.calls += 1
        step = self._script.pop(0) if self._script else ("ok", None)
        kind, payload = step
        if kind == "ok":
            return RawToolResponse(
                ok=True, code=0, message="success", payload={"code": 0, "result": {}}
            )
        if kind == "biz":
            raise BusinessError(payload[0], payload[1], task_id=ctx.task_id)
        raise RuntimeError(payload or "boom")


class _StubRegistry:
    """CapabilityRegistry 替身：固定返回 manifest 与 stub adapter。"""

    def __init__(self, manifest, adapter) -> None:
        self._manifest = manifest
        self._adapter = adapter

    def get(self, capability_id):  # noqa: ANN001
        return self._manifest

    def get_by_tool_name(self, tool_name):  # noqa: ANN001
        return self._manifest

    def list(self):
        return [self._manifest]

    def list_enabled(self):
        return [self._manifest]

    async def adapter_for(self, capability_id):  # noqa: ANN001
        return self._adapter


def _inputs() -> dict:
    return {"task_id": "T1", "trace_id": "tr-1", "image_paths": [], "auxiliary_paths": {}, "parameters": {}}


@pytest.mark.asyncio
async def test_business_error_code_preserved():
    """适配器业务错（3001 模型未就绪）原样透传，不再压平为 3002。"""
    manifest = _manifest()
    adapter = _StubAdapter([("biz", (BizCode.MODEL_NOT_READY, "model warming up"))])
    tool = InferTool(manifest, _StubRegistry(manifest, adapter))

    result = await tool.execute(_inputs())

    assert result.ok is False
    assert result.code == int(BizCode.MODEL_NOT_READY)
    assert "model warming up" in result.message


@pytest.mark.asyncio
async def test_unknown_exception_still_converges_to_failure():
    """非业务异常仍收敛为 3002（不破坏既有契约），消息保留原因。"""
    manifest = _manifest()
    adapter = _StubAdapter([("boom", None)])
    tool = InferTool(manifest, _StubRegistry(manifest, adapter))

    result = await tool.execute(_inputs())

    assert result.ok is False
    assert result.code == int(BizCode.INFERENCE_FAILED)
    assert "boom" in result.message


@pytest.mark.asyncio
async def test_pool_breaker_opens_after_consecutive_failures():
    """连续失败达阈值后熔断打开：后续调用快速失败（不触达适配器）。"""
    manifest = _manifest()
    adapter = _StubAdapter([("biz", (BizCode.MODEL_NOT_READY, "down")) for _ in range(10)])
    pool = AdapterPool(concurrency=4, breaker_threshold=3)
    tool = InferTool(manifest, _StubRegistry(manifest, adapter), pool=pool)

    # 前 3 次真实失败（第 3 次失败后熔断打开）
    for i in range(3):
        result = await tool.execute(_inputs())
        assert result.ok is False
        assert result.code == int(BizCode.MODEL_NOT_READY)
    assert adapter.calls == 3

    # 第 4 次：熔断打开，快速失败，适配器零调用
    result = await tool.execute(_inputs())
    assert result.ok is False
    assert "circuit breaker open" in result.message
    assert adapter.calls == 3


@pytest.mark.asyncio
async def test_pool_success_resets_breaker():
    """成功调用重置连续失败计数：偶发失败不累积熔断。"""
    manifest = _manifest()
    script = [("biz", (BizCode.MODEL_NOT_READY, "down")), ("ok", None)] * 3
    adapter = _StubAdapter(script)
    pool = AdapterPool(concurrency=4, breaker_threshold=3)
    tool = InferTool(manifest, _StubRegistry(manifest, adapter), pool=pool)

    results = [await tool.execute(_inputs()) for _ in range(6)]

    # 失败-成功交替：永不达到连续 3 次失败阈值，全部真实调用
    assert adapter.calls == 6
    assert [r.ok for r in results] == [False, True, False, True, False, True]


def test_build_pool_from_settings():
    """settings 携带网关治理参数时构建共享池；settings 缺失返回 None。"""
    assert _build_pool(None) is None

    class _S:
        gateway_concurrency = 8
        gateway_breaker_threshold = 2

    pool = _build_pool(_S())
    assert pool is not None

    class _Partial:
        gateway_concurrency = 8

    assert _build_pool(_Partial()) is None


def test_assemble_tools_injects_shared_pool():
    """装配链注入共享治理池（gateway_* 配置生效，不再是死代码）。"""
    manifest = _manifest()

    class _S:
        gateway_concurrency = 4
        gateway_breaker_threshold = 5

    registry = _StubRegistry(manifest, _StubAdapter([]))
    tools = assemble_tools(registry, None, settings=_S)

    infer = tools.get("detect/gov_tool")
    assert infer is not None
    assert infer._pool is not None
    # 同一次装配的所有 InferTool 共享同一池实例
    assert tools.get("detect/gov_tool")._pool is infer._pool
