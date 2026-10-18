"""provider：工厂构造 ChatOpenAI / ChatAnthropic；未知 provider 抛 BusinessError。"""
from __future__ import annotations

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

from app.agents.llm.provider import create_chat_model
from app.core.errors import BizCode, BusinessError


def test_create_openai_compatible_model():
    model = create_chat_model(
        "openai_compatible",
        base_url="https://example.com/v1",
        api_key="sk-test",
        model="qwen-test",
    )
    assert isinstance(model, ChatOpenAI)
    assert model.model == "qwen-test"


def test_create_anthropic_model():
    model = create_chat_model(
        "anthropic",
        base_url="https://maas-api.antdigital.com",
        api_key="sk-test",
        model="deepseek-v4-flash-0731",
    )
    assert isinstance(model, ChatAnthropic)
    assert model.model == "deepseek-v4-flash-0731"


def test_unknown_provider_raises():
    with pytest.raises(BusinessError) as exc_info:
        create_chat_model(
            "no_such_provider",
            base_url="https://example.com/v1",
            api_key="sk-test",
            model="m",
        )
    assert exc_info.value.code == BizCode.LLM_UNAVAILABLE


# ---------- MaaS 网关坏实例式 400 的有界重试（2026-09-16 实测抖动） ----------
from unittest.mock import AsyncMock, patch  # noqa: E402

import anthropic  # noqa: E402
import httpx  # noqa: E402
from langchain_anthropic.chat_models import AnthropicInvalidRequestError  # noqa: E402
from langchain_core.outputs import ChatResult  # noqa: E402

from app.agents.llm.provider import (  # noqa: E402
    _FlakyRetryChatAnthropic,
    _is_flaky_gateway_rejection,
)


def _resp(status: int = 400) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", "https://gw/v1/messages"))


def _flaky_exc() -> AnthropicInvalidRequestError:
    return AnthropicInvalidRequestError(
        "Error code: 400 - {'error': {'message': 'Invalid request parameters.', "
        "'type': 'InvalidParameter', 'code': 'InvalidParameter'}}",
        response=_resp(),
        body={"error": {"message": "Invalid request parameters.",
                        "type": "InvalidParameter", "code": "InvalidParameter"}},
    )


def _other_400() -> anthropic.BadRequestError:
    return anthropic.BadRequestError(
        "Error code: 400 - messages: roles must alternate",
        response=_resp(), body={"error": {"message": "roles must alternate"}},
    )


def test_flaky_detection_matches_only_invalid_parameter_400():
    assert _is_flaky_gateway_rejection(_flaky_exc()) is True
    # body 缺失时按报文兜底识别
    bare = AnthropicInvalidRequestError("InvalidParameter", response=_resp(), body=None)
    assert _is_flaky_gateway_rejection(bare) is True
    # 真实参数错误（其他 400）不重试
    assert _is_flaky_gateway_rejection(_other_400()) is False
    # 非 400 / 非请求错误不重试
    assert _is_flaky_gateway_rejection(ValueError("InvalidParameter")) is False


def test_create_anthropic_model_uses_flaky_retry_subclass():
    model = create_chat_model(
        "anthropic", base_url="https://gw", api_key="sk-test", model="qwen3.8-max",
    )
    assert isinstance(model, _FlakyRetryChatAnthropic)
    assert isinstance(model, ChatAnthropic)  # 既有断言口径不破坏


@pytest.mark.asyncio
async def test_agenerate_retries_flaky_400_then_succeeds():
    model = create_chat_model(
        "anthropic", base_url="https://gw", api_key="sk-test", model="m",
    )
    ok = ChatResult(generations=[])
    calls = []

    async def flaky_twice(*a, **kw):
        calls.append(1)
        if len(calls) <= 2:
            raise _flaky_exc()
        return ok

    with patch.object(ChatAnthropic, "_agenerate", new=flaky_twice), \
         patch("app.agents.llm.provider.asyncio.sleep", new=AsyncMock()):
        result = await model._agenerate([])
    assert result is ok
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_agenerate_gives_up_after_bounded_retries():
    model = create_chat_model(
        "anthropic", base_url="https://gw", api_key="sk-test", model="m",
    )
    calls = []

    async def always_flaky(*a, **kw):
        calls.append(1)
        raise _flaky_exc()

    with patch.object(ChatAnthropic, "_agenerate", new=always_flaky), \
         patch("app.agents.llm.provider.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(AnthropicInvalidRequestError):
            await model._agenerate([])
    assert len(calls) == 4  # 首次 + 3 次重试后原样抛出


@pytest.mark.asyncio
async def test_agenerate_does_not_retry_real_parameter_errors():
    model = create_chat_model(
        "anthropic", base_url="https://gw", api_key="sk-test", model="m",
    )
    calls = []

    async def real_400(*a, **kw):
        calls.append(1)
        raise _other_400()

    with patch.object(ChatAnthropic, "_agenerate", new=real_400):
        with pytest.raises(anthropic.BadRequestError):
            await model._agenerate([])
    assert len(calls) == 1  # 真实 400 不浪费重试


def test_generate_sync_path_retries_flaky_400(monkeypatch):
    model = create_chat_model(
        "anthropic", base_url="https://gw", api_key="sk-test", model="m",
    )
    monkeypatch.setattr("app.agents.llm.provider.time.sleep", lambda _s: None)
    ok = ChatResult(generations=[])
    calls = []

    def flaky_once(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise _flaky_exc()
        return ok

    with patch.object(ChatAnthropic, "_generate", new=flaky_once):
        result = model._generate([])
    assert result is ok
    assert len(calls) == 2
