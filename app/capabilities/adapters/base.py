"""AI 网关：统一适配层抽象与调用治理公共设施。

设计（对应需求文档 §5.2）：
- ToolAdapter：统一工具接口（health / invoke），屏蔽三类接入形态差异
- AdapterContext：一次调用生命周期上下文（task_id/trace_id/治理控制）
- 治理：并发信号量、有限重试、熔断（进程内实现，生产可换 Redis 分布式版）
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import BizCode, BusinessError


@dataclass
class RawToolResponse:
    """算法工具原始响应（规范 §7 外壳解析后）。"""

    ok: bool
    code: int
    message: str
    task_id: str | None = None
    trace_id: str | None = None
    performance_ms: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)  # 完整外壳（含 result/artifacts）
    latency_ms: int = 0


class AdapterContext:
    """单次工具调用的上下文，携带治理参数（供适配器实现重试/超时）。"""

    def __init__(
        self,
        task_id: str,
        trace_id: str,
        timeout_s: float,
        retry_times: int,
    ) -> None:
        self.task_id = task_id
        self.trace_id = trace_id
        self.timeout_s = timeout_s
        self.retry_times = retry_times


class ToolAdapter(ABC):
    """算法工具适配器抽象：HTTP API / 容器化服务 / 本地软件桥接统一入口。"""

    tool_name: str
    scene: str

    @abstractmethod
    async def health(self) -> tuple[bool, str]:
        """健康检查（规范 §5.1）。"""

    @abstractmethod
    async def invoke(
        self,
        ctx: AdapterContext,
        image_paths: list[str],
        params: dict[str, Any],
        *,
        auxiliary: dict[str, str] | None = None,
    ) -> RawToolResponse:
        """调用推理（规范 §5.2）。成功失败都以 RawToolResponse 返回。"""


class CircuitBreaker:
    """简易熔断：连续失败达到阈值后短路，冷却期后试恢复。"""

    def __init__(self, threshold: int, cooldown_s: float = 30.0) -> None:
        self._threshold = threshold
        self._cooldown_s = cooldown_s
        self._failures = 0
        self._open_since: float | None = None
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            if self._open_since is not None:
                if time.monotonic() - self._open_since >= self._cooldown_s:
                    self._open_since = None  # 半开：放行一次试探
                else:
                    raise BusinessError(
                        BizCode.ADAPTER_ERROR,
                        "circuit breaker open, service temporarily unavailable",
                    )

    async def report(self, success: bool) -> None:
        async with self._lock:
            if success:
                self._failures = 0
                self._open_since = None
                return
            self._failures += 1
            if self._failures >= self._threshold and self._open_since is None:
                self._open_since = time.monotonic()


class AdapterPool:
    """适配器池：per-tool 信号量 + 熔断器，按 tool_name 共享。"""

    def __init__(self, concurrency: int, breaker_threshold: int) -> None:
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._breakers: dict[str, CircuitBreaker] = {}
        self._concurrency = concurrency
        self._breaker_threshold = breaker_threshold

    def semaphore(self, tool_name: str) -> asyncio.Semaphore:
        return self._semaphores.setdefault(tool_name, asyncio.Semaphore(self._concurrency))

    def breaker(self, tool_name: str) -> CircuitBreaker:
        return self._breakers.setdefault(
            tool_name, CircuitBreaker(self._breaker_threshold)
        )