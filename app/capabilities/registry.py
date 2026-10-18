"""能力注册中心（纯 DB 驱动，对应 docs/a.md §12 落库 + §4 内存版演进）。

数据流（决策树定案）：
- DB capability_registry 为唯一事实源；启动一次性读全表入内存缓存；
- 自动刷新：后台轻量任务每 POLL_INTERVAL_S 秒比对 MAX(version)（单行聚合），
  有变化才全量重载缓存并重建 Tool 装配；
- reload() 显式立即刷新（POST /api/v1/capabilities/reload 语义）；
- 场景路由 lookup(scene) 走内存索引（enabled 过滤），零查询消耗；
- 空表（未跑 sync_capabilities 种子）：注册中心为空，任务路由 4004，
  health 巡检标红提示「no capability configured」（B1 运维时序，不自动种子）。
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.api.schemas.capability import CapabilityHealth
from app.capabilities.factory import AdapterFactory
from app.core.errors import BizCode, BusinessError
from app.models.capability import CapabilityManifest
from app.core.logger import get_logger
from app.repositories.capability_repo import CapabilityRepo

logger = get_logger("app.capabilities.registry")

POLL_INTERVAL_S = 15  # 人工注册频度低，15s 轮询足够平滑（决策 7/A）


class CapabilityRegistry:
    """DB 驱动能力注册中心：内存缓存 + 自动刷新 + 场景路由。"""

    def __init__(
        self,
        repo: CapabilityRepo,
        *,
        poll_interval_s: float = POLL_INTERVAL_S,
        on_refresh: Any = None,
    ) -> None:
        self._repo = repo
        self._poll_interval_s = poll_interval_s
        self.on_refresh = on_refresh  # 可选钩子：刷新后重建 Tool Registry 装配
        self._manifests: dict[str, CapabilityManifest] = {}
        self._scene_index: dict[str, list[CapabilityManifest]] = {}
        self._adapters: dict[str, Any] = {}  # capability_id -> adapter（refresh 清空重建）
        self._last_version = 0
        self._task: asyncio.Task | None = None

    # ---------- 生命周期 ----------
    async def start(self) -> None:
        """启动：加载全表 + 开启后台轮询（幂等）。"""
        await self.refresh()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._poll_loop())
            logger.info("capability registry polling started (interval=%ss)", self._poll_interval_s)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # ---------- 加载 / 刷新 ----------
    async def refresh(self) -> int:
        """全量重载（启动 / reload / 轮询有变化时）。返回能力数量。"""
        records = await self._repo.list_all()
        manifests: dict[str, CapabilityManifest] = {}
        for rec in records:
            try:
                manifests[rec.capability_id] = CapabilityManifest.from_record(rec)
            except Exception:  # noqa: BLE001
                logger.exception("bad capability record skipped: %s", rec.capability_id)

        self._manifests = manifests
        self._rebuild_indexes()
        self._adapters.clear()  # manifest 变更 => 旧 adapter 作废（重建 httpx 客户端）
        self._last_version = await self._repo.max_version()
        logger.info(
            "capability registry loaded: total=%d, enabled=%d, version=%d",
            len(self._manifests),
            sum(1 for m in self._manifests.values() if m.status == "enabled"),
            self._last_version,
        )
        if self.on_refresh is not None:
            try:
                await self.on_refresh(self._manifests.values())
            except Exception:  # noqa: BLE001
                logger.exception("on_refresh hook failed")
        return len(self._manifests)

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._poll_interval_s)
            try:
                current = await self._repo.max_version()
                if current > self._last_version:
                    logger.info("capability version changed %d -> %d, reloading", self._last_version, current)
                    await self.refresh()
            except Exception:  # noqa: BLE001
                logger.exception("capability version poll failed (will retry)")

    # ---------- 查询 ----------
    def list(self) -> list[CapabilityManifest]:
        return list(self._manifests.values())

    def list_enabled(self) -> list[CapabilityManifest]:
        return [m for m in self._manifests.values() if m.status == "enabled"]

    def get(self, capability_id: str) -> CapabilityManifest | None:
        return self._manifests.get(capability_id)

    def get_by_tool_name(self, tool_name: str) -> CapabilityManifest | None:
        for manifest in self._manifests.values():
            if manifest.tool_name == tool_name:
                return manifest
        return None

    def lookup(self, scene: str) -> CapabilityManifest | None:
        """场景路由：返回该场景下首个启用能力（决策 5：disabled/maintenance 不参与路由）。"""
        for manifest in self._scene_index.get(scene, []):
            if manifest.status == "enabled":
                return manifest
        return None

    # ---------- 健康 ----------
    async def health_all(self) -> list[CapabilityHealth]:
        if not self._manifests:
            return [CapabilityHealth(tool_name="", healthy=False, message="no capability configured")]

        results: list[CapabilityHealth] = []
        for manifest in self.list_enabled():
            adapter = await self._adapter_for(manifest)
            try:
                ok, message = await adapter.health()
                results.append(CapabilityHealth(tool_name=manifest.tool_name, healthy=ok, message=message))
            except Exception as exc:  # noqa: BLE001
                results.append(
                    CapabilityHealth(tool_name=manifest.tool_name, healthy=False, message=str(exc))
                )
        return results

    # ---------- 适配器 ----------
    def _rebuild_indexes(self) -> None:
        index: dict[str, list[CapabilityManifest]] = {}
        for manifest in self._manifests.values():
            index.setdefault(manifest.scene, []).append(manifest)
        self._scene_index = index

    async def adapter_for(self, capability_id: str) -> Any:
        """为能力创建/复用适配器（按 capability_id 缓存，refresh 后整体重建）。"""
        manifest = self._manifests.get(capability_id)
        if manifest is None:
            raise LookupError(f"capability not found: {capability_id}")
        cached = self._adapters.get(capability_id)
        if cached is not None:
            return cached
        adapter = await self._adapter_for(manifest)
        self._adapters[capability_id] = adapter
        return adapter

    async def _adapter_for(self, manifest: CapabilityManifest) -> Any:
        """按 manifest 创建适配器（http / mock / local_bridge / model_service 统一走工厂）。"""
        try:
            return AdapterFactory.create(manifest)
        except ValueError as exc:
            raise BusinessError(
                BizCode.INTERNAL_ERROR, f"unknown adapter_type: {exc}"
            ) from exc