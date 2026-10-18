"""provider 工厂：max_tokens 透传到具体客户端。

生产环境的模型会整段吐 thinking 块后才有 text（qwen3.8-max via MaaS 网关），
输出预算不足时 thinking 吃满、text 不产生 → 上层收到空回复。
显式 max_tokens 保证文本有生成空间。
"""
from __future__ import annotations

from app.agents.llm.provider import create_chat_model


def test_anthropic_model_carries_explicit_max_tokens():
    model = create_chat_model(
        "anthropic",
        base_url="https://maas-api.antdigital.com",
        api_key="sk-test",
        model="qwen3.8-max",
        max_tokens=8192,
    )

    assert model.max_tokens == 8192


def test_anthropic_model_defaults_max_tokens_to_gateway_default():
    """不传 max_tokens 时由 langchain-anthropic 补齐其默认 4096（chat 模型维持现状）。"""
    model = create_chat_model(
        "anthropic",
        base_url="https://maas-api.antdigital.com",
        api_key="sk-test",
        model="deepseek-v4-flash-0731",
    )

    assert model.max_tokens == 4096


def test_openai_compatible_model_carries_explicit_max_tokens():
    model = create_chat_model(
        "openai_compatible",
        base_url="https://example.com/v1",
        api_key="sk-test",
        model="qwen-vl",
        max_tokens=8192,
    )

    assert model.max_tokens == 8192