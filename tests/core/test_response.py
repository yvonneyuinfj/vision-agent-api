"""统一响应外壳测试：对齐规范 §7。"""
from __future__ import annotations

from app.core import context
from app.core.errors import BizCode
from app.core.response import StandardEnvelope


def test_success_envelope():
    # Arrange
    context.set_trace_id("trace-test")
    # Act
    env = StandardEnvelope.ok({"a": 1})
    # Assert
    data = env.model_dump()
    assert data["code"] == 0
    assert data["trace_id"] == "trace-test"
    assert data["result"] == {"a": 1}
    assert data["tool"]["tool_name"] == "quality_inspection_agent"


def test_error_envelope_maps_bizcode():
    # Arrange
    context.set_trace_id("trace-test-2")
    # Act
    env = StandardEnvelope.err(BizCode.MODEL_NOT_READY, "model cold")
    data = env.model_dump()
    # Assert（规范 §7：错误响应必含 result 字段且为 null）
    assert data["code"] == int(BizCode.MODEL_NOT_READY)
    assert "result" in data
    assert data["result"] is None