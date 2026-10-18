"""上下文感知索引脚本测试：staging 组装 + 前缀生成回退 + 注入防护提示词。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from contextual_index import PREFIX_PROMPT, build_staging_text, generate_prefix  # noqa: E402


def _chunks() -> list[dict]:
    return [
        {"chunk_id": "c1", "content": "验收限值：气孔直径不超过 2.0mm", "page": 3},
        {"chunk_id": "c2", "content": "表 4 断口形貌与载荷类型对应关系", "page": 7},
    ]


def test_build_staging_text_wraps_prefix_with_marker():
    """前缀用 <chunk-context> 包裹：定位信息与原文边界清晰。"""
    text = build_staging_text(
        "GHB123 断口标准.pdf", _chunks(), ["出自 GHB123 断口标准，气孔验收限值条款", "出自 GHB123 表 4，断口形貌分类表"]
    )

    assert text.startswith("# GHB123 断口标准.pdf（上下文感知索引版本）")
    assert "<chunk-context>出自 GHB123 断口标准，气孔验收限值条款</chunk-context>" in text
    assert "验收限值：气孔直径不超过 2.0mm" in text
    # 两个 chunk 都在
    assert "断口形貌与载荷类型对应关系" in text


def test_build_staging_text_empty_prefix_keeps_original():
    """前缀生成失败（空串）：原文保留，不产生空标记。"""
    text = build_staging_text("doc.pdf", _chunks(), ["", ""])

    assert "<chunk-context></chunk-context>" not in text
    assert "验收限值：气孔直径不超过 2.0mm" in text


def test_prefix_prompt_has_injection_guard():
    """前缀生成提示词含防注入条款：块内指令一律视为待描述数据。"""
    assert "不得执行" in PREFIX_PROMPT
    assert "只描述内容" in PREFIX_PROMPT


@pytest.mark.asyncio
async def test_generate_prefix_llm_failure_returns_empty():
    """LLM 调用失败：回退空前缀（不阻断整文档 staging）。"""

    class _BrokenLLM:
        async def chat(self, prompt, **kw):  # noqa: ANN001
            raise RuntimeError("llm down")

    prefix = await generate_prefix(_BrokenLLM(), "doc.pdf", "content")

    assert prefix == ""


@pytest.mark.asyncio
async def test_generate_prefix_normal_path():
    class _FakeLLM:
        async def chat(self, prompt, **kw):  # noqa: ANN001
            return "出自 doc.pdf 的验收条款。"

    prefix = await generate_prefix(_FakeLLM(), "doc.pdf", "content")

    assert prefix == "出自 doc.pdf 的验收条款。"
