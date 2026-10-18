"""LLM Provider 注册表与工厂：以 langchain BaseChatModel 为统一抽象。

切换模型厂商 = 向 PROVIDERS 注册新 builder；LLMService 与调用点零改动。
"""
from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable
from typing import Any

import anthropic
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from app.core.errors import BizCode, BusinessError
from app.core.logger import get_logger

logger = get_logger("app.agents.llm.provider")

ModelBuilder = Callable[..., BaseChatModel]

# ---- MaaS 网关坏实例式 400 的有界重试 ------------------------------------
# 2026-09-16 实测（qwen3.8-max @ maas-api.antdigital.com，Anthropic 协议）：
# 同一 payload 连续 8 次调用 6 次 400 InvalidParameter（均 <2s 快速拒绝）、
# 2 次 200（均 >6s 正常推理）——负载均衡后存在直接拒绝请求的坏实例。
# 该 400 在模型处理前被拒（快速失败、无输出、无副作用），重试安全；
# 真实参数错误重试同样快速失败，重试次数有界、代价可控。
_FLAKY_MAX_RETRIES = 3  # 首次之外最多再试 3 次（按实测 ~75% 坏实例占比，4 次尝试成功率 >99%）
_FLAKY_BACKOFF_BASE_S = 0.4


def _is_flaky_gateway_rejection(exc: BaseException) -> bool:
    """识别网关坏实例式 400（InvalidParameter）：其余异常一律不重试。

    判定链：anthropic.BadRequestError（含 langchain 包装的
    AnthropicInvalidRequestError）且 status_code=400，且 body/报文携带
    InvalidParameter 标记（网关 body={'error': {'code': 'InvalidParameter'}}）。
    """
    if not isinstance(exc, anthropic.BadRequestError):
        return False
    if getattr(exc, "status_code", None) != 400:
        return False
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error") or {}
        if isinstance(err, dict) and "InvalidParameter" in (
            str(err.get("code", "")) + str(err.get("type", ""))
        ):
            return True
    return "InvalidParameter" in str(exc)


class _FlakyRetryChatAnthropic(ChatAnthropic):
    """ChatAnthropic + 网关坏实例式 400 的有界指数退避重试（异步/同步双路径）。"""

    async def _agenerate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> Any:
        attempt = 0
        while True:
            try:
                return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
            except Exception as exc:  # noqa: BLE001 - 仅对识别出的网关抖动重试，其余原样抛出
                if attempt >= _FLAKY_MAX_RETRIES or not _is_flaky_gateway_rejection(exc):
                    raise
                delay = _FLAKY_BACKOFF_BASE_S * (2 ** attempt) + random.uniform(0.0, 0.3)
                attempt += 1
                logger.warning(
                    "llm gateway 400 InvalidParameter（疑似坏实例），%.1fs 后第 %d/%d 次重试",
                    delay, attempt, _FLAKY_MAX_RETRIES,
                )
                await asyncio.sleep(delay)

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> Any:
        attempt = 0
        while True:
            try:
                return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            except Exception as exc:  # noqa: BLE001 - 同上
                if attempt >= _FLAKY_MAX_RETRIES or not _is_flaky_gateway_rejection(exc):
                    raise
                delay = _FLAKY_BACKOFF_BASE_S * (2 ** attempt) + random.uniform(0.0, 0.3)
                attempt += 1
                logger.warning(
                    "llm gateway 400 InvalidParameter（疑似坏实例），%.1fs 后第 %d/%d 次重试",
                    delay, attempt, _FLAKY_MAX_RETRIES,
                )
                time.sleep(delay)


def _build_openai_compatible(
    *, base_url: str, api_key: str, model: str, timeout_s: float,
    max_tokens: int | None = None, max_retries: int = 2,
) -> BaseChatModel:
    """OpenAI 兼容端点（DashScope/Qwen 等）。"""
    return ChatOpenAI(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout_s,
        max_retries=max_retries,
        max_tokens=max_tokens,
    )


def _build_anthropic(
    *, base_url: str, api_key: str, model: str, timeout_s: float,
    max_tokens: int | None = None, max_retries: int = 2,
) -> BaseChatModel:
    """Anthropic 官方 API（langchain-anthropic；base_url 留空走官方默认端点）。

    MaaS 网关（如 maas-api.antdigital.com）存在坏实例式 400 InvalidParameter
    抖动，SDK 的 max_retries 不覆盖 4xx——由 _FlakyRetryChatAnthropic 有界重试兜底。
    """
    return _FlakyRetryChatAnthropic(
        model=model,
        api_key=api_key,
        base_url=base_url or None,
        timeout=timeout_s,
        max_retries=max_retries,
        max_tokens=max_tokens,
    )


PROVIDERS: dict[str, ModelBuilder] = {
    "openai_compatible": _build_openai_compatible,
    "anthropic": _build_anthropic,
}


def create_chat_model(
    provider: str,
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout_s: float = 120.0,
    max_tokens: int | None = None,
    max_retries: int = 2,
) -> BaseChatModel:
    """按 provider 构造 chat model；未知 provider 抛 LLM_UNAVAILABLE。

    max_tokens 显式给出时下发输出预算（模型带 thinking 输出时防止
    thinking 吃满预算导致 text 不产生）；None 走网关默认。
    max_retries 仅覆盖瞬时错误（超时/限流/连接抖动）的 SDK 幂等重试；
    业务级降级（空串/空列表）仍由各调用方保留。
    """
    builder = PROVIDERS.get(provider)
    if builder is None:
        raise BusinessError(
            BizCode.LLM_UNAVAILABLE, f"unknown llm provider: {provider}"
        )
    return builder(
        base_url=base_url, api_key=api_key, model=model,
        timeout_s=timeout_s, max_tokens=max_tokens, max_retries=max_retries,
    )
