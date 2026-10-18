"""能力注册中心路由（a.md：能力发现 / 健康巡检 / 热刷新 / 批次 3 写侧）。

路由层只做路由+校验+外壳（StandardEnvelope/PageResult），业务逻辑在 CapabilityService。
写端点声明顺序：/health 必须先于 /{capability_id}，否则 "health" 会被当作 ID。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.config import get_settings
from app.models.capability import (
    CapabilityCreateBody,
    CapabilityDetail,
    CapabilityHealth,
    CapabilityPatchBody,
    CapabilityStatusBody,
    CapabilitySummary,
    DryRunBody,
    DryRunResult,
)
from app.core.errors import BizCode, BusinessError
from app.core.response import PageMeta, PageResult, StandardEnvelope
from app.services.capability_service import CapabilityService

router = APIRouter(prefix="/api/v1/capabilities", tags=["capabilities"])


def _svc(request: Request) -> CapabilityService:
    c = request.app.state.container
    if c is None or c.registry is None:
        raise BusinessError(BizCode.INTERNAL_ERROR, "container not ready")
    return CapabilityService(c.registry, getattr(c, "capability_repo", None))


def _operator(request: Request, operator: str | None) -> str | None:
    """留痕操作人（决策 46）：表单自报优先，缺省取登录会话用户名，再缺省「未登录」。

    自报值不做身份认证，前后值审计属 G4-01；会话用户名仅在 auth_enabled=True 时可信。
    """
    if operator:
        return operator
    settings = get_settings()
    svc = getattr(request.app.state, "auth", None)
    if svc is not None and settings.auth_enabled:
        return svc.session_from_cookie(request.cookies.get(settings.auth_cookie_name))
    return None


@router.get("", response_model=StandardEnvelope[PageResult[CapabilitySummary]])
async def list_capabilities(
    request: Request,
    scene: str | None = None,
    enabled_only: bool | None = None,
) -> StandardEnvelope[PageResult[CapabilitySummary]]:
    service = _svc(request)
    items = service.list(scene=scene, enabled_only=enabled_only)
    return StandardEnvelope.ok(
        PageResult(items=items, meta=PageMeta(total=len(items), page=1, page_size=len(items) or 1))
    )


@router.get("/health", response_model=StandardEnvelope[list[CapabilityHealth]])
async def health_all(request: Request) -> StandardEnvelope[list[CapabilityHealth]]:
    service = _svc(request)
    results = await service.health_all()
    return StandardEnvelope.ok(results)


@router.post("/reload")
async def reload_capabilities(request: Request) -> StandardEnvelope[int]:
    service = _svc(request)
    count = await service.reload()
    return StandardEnvelope.ok(count, message=f"reloaded {count} capabilities from DB")


@router.post("/dry-run", response_model=StandardEnvelope[DryRunResult])
async def dry_run_capability(
    request: Request, body: DryRunBody
) -> StandardEnvelope[DryRunResult]:
    """三段连通性（决策 34）：只探不写，成功回执带 canonical 样例与快照指纹。"""
    service = _svc(request)
    return StandardEnvelope.ok(await service.dry_run(body))


@router.post("", response_model=StandardEnvelope[dict])
async def create_capability(
    request: Request, body: CapabilityCreateBody
) -> StandardEnvelope[dict]:
    """登记新能力（决策 27/35/36/37）：默认 disabled，干跑过才允许 enabled。"""
    body.operator = _operator(request, body.operator)
    service = _svc(request)
    return StandardEnvelope.ok(await service.create(body), message="capability registered")


@router.patch("/{capability_id}", response_model=StandardEnvelope[dict])
async def patch_capability(
    request: Request, capability_id: str, body: CapabilityPatchBody
) -> StandardEnvelope[dict]:
    """字段级 patch（决策 39/42）：未出现的列保留，显式 null 清空，身份列不可写。"""
    body.operator = _operator(request, body.operator)
    service = _svc(request)
    return StandardEnvelope.ok(await service.patch(capability_id, body), message="capability updated")


@router.post("/{capability_id}/status", response_model=StandardEnvelope[dict])
async def set_capability_status(
    request: Request, capability_id: str, body: CapabilityStatusBody
) -> StandardEnvelope[dict]:
    """启停（决策 45）：独立端点 + 前端二次确认；启用前置 health 探测。"""
    body.operator = _operator(request, body.operator)
    service = _svc(request)
    return StandardEnvelope.ok(await service.set_status(capability_id, body), message="status changed")


@router.get("/{capability_id}", response_model=StandardEnvelope[CapabilityDetail])
async def get_capability(
    request: Request, capability_id: str
) -> StandardEnvelope[CapabilityDetail]:
    """详情（决策 42/43）：下发 version + snapshot，headers 只回掩码。"""
    service = _svc(request)
    return StandardEnvelope.ok(await service.detail(capability_id))

