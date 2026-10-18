"""能力服务：能力编排下沉（a.md 分层铁律——路由层只做路由+校验+外壳）。

读侧（list/health/reload）走内存注册中心；写侧（批次 3，决策 27–50）走
CapabilityRepo 的列级 patch + 提交后立即 refresh()，校验规则集中在 capability_gate。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx

from app.capabilities.adapters.http_adapter import resolve_base_url
from app.capabilities.adapters.tool_profiles import apply_tool_profile
from app.capabilities.registry import CapabilityRegistry
from app.core.config import BASE_DIR
from app.core.errors import BizCode, BusinessError
from app.models.capability import (
    CapabilityCreateBody,
    CapabilityDetail,
    CapabilityHealth,
    CapabilityPatchBody,
    CapabilityStatusBody,
    CapabilitySummary,
    DryRunBody,
    DryRunResult,
    DryRunStep,
)
from app.repositories.capability_repo import CapabilityRepo, STATUS_ENABLED
from app.services.capability_gate import (
    ALLOWED_SCENES,
    append_change_log,
    mask_headers,
    merge_meta_patch,
    validate_meta,
    validate_response_sample,
)

# 干跑样例只允许取仓库内已交付的样本，禁止任意绝对路径。
# .opi＝X光/DR 的序列帧输入（除图像外唯一已交付的非图物料）。
_SAMPLE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".opi"}
_MIME_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff",
    ".opi": "application/octet-stream",
}
_SAMPLE_ROOTS = ("local-tools", "data")

# 运行回执台账（决策 27/41）：快照只是 endpoint+headers 的哈希，任何调用方都能自行算出，
# 所以保存门禁只认「本进程内一次成功完整试运行发放过的快照」，防止离线拼指纹绕过。
_RECEIPT_TTL_S = 1800.0
_receipts: dict[str, float] = {}


def issue_receipt(snapshot: str) -> None:
    """完整试运行通过后发放回执；顺带清退过期项（单进程 uvicorn，够用）。"""
    now = time.monotonic()
    _receipts[snapshot] = now
    for stale in [key for key, at in _receipts.items() if now - at > _RECEIPT_TTL_S]:
        _receipts.pop(stale, None)


def has_receipt(snapshot: str | None) -> bool:
    if not snapshot:
        return False
    at = _receipts.get(snapshot)
    if at is None:
        return False
    if time.monotonic() - at > _RECEIPT_TTL_S:
        _receipts.pop(snapshot, None)
        return False
    return True


def reset_receipts() -> None:
    """测试用：清空回执台账。"""
    _receipts.clear()


def snapshot_of(endpoint: str, headers: Any) -> str:
    """决策 41：endpoint + headers 的字段快照指纹（推理干跑不重跑的比对键）。"""
    canonical = json.dumps(
        {"endpoint": endpoint or "", "headers": headers or {}},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _resolve_sample_images(rel_paths: str) -> list[Path]:
    """把前端传来的样例路径钉死在仓库内（防 ../ 穿越与绝对路径读取），支持多帧。"""
    tokens = [t for t in rel_paths.replace(",", " ").replace("\n", " ").split() if t]
    if not tokens:
        raise BusinessError(BizCode.INVALID_REQUEST, "至少提供一个样例物料路径")
    root = BASE_DIR.resolve()
    allowed = [(root / sub).resolve() for sub in _SAMPLE_ROOTS]
    resolved: list[Path] = []
    for token in tokens:
        candidate = (root / token).resolve()
        if candidate.suffix.lower() not in _SAMPLE_EXTS or not candidate.is_file():
            raise BusinessError(BizCode.INVALID_REQUEST, f"样例物料不存在或扩展名不支持：{token}")
        if not any(candidate.is_relative_to(base) for base in allowed):
            raise BusinessError(
                BizCode.INVALID_REQUEST, f"样例物料必须位于 {'/'.join(_SAMPLE_ROOTS)} 之下：{token}"
            )
        resolved.append(candidate)
    return resolved


class CapabilityService:
    """能力查询/巡检/刷新 + 写侧（登记/编辑/启停/干跑）门面。"""

    def __init__(self, registry: CapabilityRegistry, repo: CapabilityRepo | None = None) -> None:
        self._registry = registry
        self._repo = repo

    # ---------- 读 ----------
    def list(self, scene: str | None = None, enabled_only: bool | None = None) -> list[CapabilitySummary]:
        manifests = self._registry.list()
        if scene:
            manifests = [m for m in manifests if m.scene == scene]
        if enabled_only:
            manifests = [m for m in manifests if m.status == "enabled"]
        return [
            CapabilitySummary(
                capability_id=m.capability_id,
                tool_name=m.tool_name,
                tool_version=m.tool_version,
                model_version=m.model_version,
                display_name=m.description or m.tool_name,
                scene=m.scene,
                capability_type=m.capability_type,
                adapter_type=m.adapter_type,
                status=m.status,
                note=m.note,
            )
            for m in manifests
        ]

    async def health_all(self) -> list[CapabilityHealth]:
        return await self._registry.health_all()

    async def reload(self) -> int:
        return await self._registry.refresh()

    async def detail(self, capability_id: str) -> CapabilityDetail:
        """详情（决策 43）：读方向只回掩码 headers，同时下发乐观锁 version 与快照。"""
        rec = await self._require_repo().get(capability_id)
        if rec is None:
            raise BusinessError(BizCode.TASK_NOT_FOUND, f"capability not found: {capability_id}")
        raw_meta = _loads(rec.meta_json)
        headers = raw_meta.get("headers") or {}
        change_log = raw_meta.get("change_log")
        return CapabilityDetail(
            capability_id=rec.capability_id,
            tool_name=rec.tool_name,
            scene=rec.scene,
            capability_type=rec.capability_type,
            adapter_type=rec.adapter_type,
            endpoint=rec.endpoint,
            health_endpoint=rec.health_endpoint,
            tool_version=rec.tool_version,
            model_version=rec.model_version,
            api_version=rec.api_version,
            input_schema=_loads(rec.input_schema) or None,
            output_schema=_loads(rec.output_schema) or None,
            timeout_s=rec.timeout_s,
            max_retry=rec.max_retry,
            status=rec.status,
            description=rec.description,
            model_weights_sha256=rec.model_weights_sha256,
            meta={k: v for k, v in raw_meta.items() if k != "headers"},
            headers_masked=mask_headers(headers),
            change_log=[e for e in change_log if isinstance(e, dict)] if isinstance(change_log, list) else [],
            version=rec.version,
            snapshot=snapshot_of(rec.endpoint, headers),
        )

    # ---------- 写 ----------
    async def create(self, body: CapabilityCreateBody) -> dict[str, Any]:
        repo = self._require_repo()
        _check_scene(body.scene)
        existed = await repo.get(body.capability_id)
        if existed is not None:
            raise BusinessError(
                BizCode.TASK_STATE_CONFLICT, f"capability_id 已存在：{body.capability_id}（登记不做隐式更新）"
            )
        self._check_tool_name_unique(body.tool_name, exclude=None)

        raw_meta = dict(body.meta or {})
        problems = validate_meta(raw_meta)
        cap_type = body.capability_type
        if body.output_schema is not None:
            problems += validate_response_sample(body.output_schema, cap_type)
        if body.status == STATUS_ENABLED:
            problems += self._require_dry_run_gate(body.endpoint, raw_meta.get("headers"), body.expected_snapshot)
        if problems:
            raise BusinessError(BizCode.INVALID_REQUEST, "；".join(problems))

        append_change_log(
            raw_meta, reason=body.change_reason, operator=body.operator,
            action="create", changed_fields=["*"],
        )
        await repo.insert(
            capability_id=body.capability_id,
            tool_name=body.tool_name,
            scene=body.scene,
            capability_type=cap_type,
            adapter_type=body.adapter_type,
            endpoint=body.endpoint,
            health_endpoint=body.health_endpoint,
            tool_version=body.tool_version,
            model_version=body.model_version,
            api_version=body.api_version,
            input_schema=body.input_schema,
            output_schema=body.output_schema,
            timeout_s=body.timeout_s,
            max_retry=body.max_retry,
            status=body.status,
            description=body.description,
            model_weights_sha256=body.model_weights_sha256,
            meta=raw_meta,
        )
        count = await self._registry.refresh()  # 决策 38：同进程立即生效
        return {"capability_id": body.capability_id, "status": body.status, "capabilities": count}

    async def patch(self, capability_id: str, body: CapabilityPatchBody) -> dict[str, Any]:
        """字段级 patch（决策 39）+ 乐观锁（决策 42）+ 分层门禁（决策 41）。"""
        repo = self._require_repo()
        rec = await repo.get(capability_id)
        if rec is None:
            raise BusinessError(BizCode.TASK_NOT_FOUND, f"capability not found: {capability_id}")
        if rec.version != body.expected_version:
            raise BusinessError(
                BizCode.TASK_STATE_CONFLICT,
                f"能力已被他人改动（当前 version={rec.version}，表单快照 version={body.expected_version}），请刷新后重填",
            )

        submitted = body.model_dump(exclude_unset=True)
        for key in ("expected_version", "change_reason", "operator", "expected_snapshot", "meta"):
            submitted.pop(key, None)

        raw_meta = _loads(rec.meta_json)
        fields: dict[str, Any] = dict(submitted)
        if "scene" in fields and fields["scene"] is not None:
            _check_scene(fields["scene"])

        if not submitted and body.meta is None:
            raise BusinessError(BizCode.INVALID_REQUEST, "没有任何可写字段")

        # headers 走逐键合并（决策 43），其余 meta 子键整块替换（决策 40）
        if body.meta is not None:
            try:
                raw_meta = merge_meta_patch(raw_meta, body.meta)
            except ValueError as exc:  # 表单侧结构错误 → 400，不是 500
                raise BusinessError(BizCode.INVALID_REQUEST, str(exc)) from exc
            problems = validate_meta(raw_meta)
            if problems:
                raise BusinessError(BizCode.INVALID_REQUEST, "；".join(problems))
        cap_type = fields.get("capability_type") or rec.capability_type
        if "output_schema" in fields:
            sample = fields["output_schema"]
            if sample is not None:
                problems = validate_response_sample(sample, cap_type)
                if problems:
                    raise BusinessError(
                        BizCode.INVALID_REQUEST, "期望响应样例未过门禁：" + "；".join(problems)
                    )

        endpoint = fields.get("endpoint", rec.endpoint)
        headers = raw_meta.get("headers") or {}
        if rec.status == STATUS_ENABLED and snapshot_of(endpoint, headers) != snapshot_of(rec.endpoint, _loads(rec.meta_json).get("headers")):
            problems = self._require_dry_run_gate(endpoint, headers, body.expected_snapshot)
            if problems:
                raise BusinessError(BizCode.INVALID_REQUEST, "；".join(problems))

        # meta_json 只是 change_log / 请求头的落库载体，对外报用户可读的 meta.<子键>
        visible = sorted(set(fields) | {f"meta.{k}" for k in (body.meta or {})})
        append_change_log(
            raw_meta, reason=body.change_reason, operator=body.operator,
            action="update", changed_fields=visible,
        )
        fields["meta_json"] = raw_meta

        changed = await repo.update_fields(capability_id, fields, expected_version=body.expected_version)
        if not changed:
            raise BusinessError(
                BizCode.TASK_STATE_CONFLICT,
                "写入与 15s 轮询撞车（version 已变），请刷新后重试",
            )
        count = await self._registry.refresh()
        return {"capability_id": capability_id, "fields": visible, "capabilities": count}

    async def set_status(self, capability_id: str, body: CapabilityStatusBody) -> dict[str, Any]:
        """启停（决策 45）：独立端点；启用前先跑一次便宜的 health 探测。"""
        repo = self._require_repo()
        rec = await repo.get(capability_id)
        if rec is None:
            raise BusinessError(BizCode.TASK_NOT_FOUND, f"capability not found: {capability_id}")
        if body.status == STATUS_ENABLED:
            ok, message, _latency = await self._probe_health(
                rec.endpoint, rec.health_endpoint, _loads(rec.meta_json).get("headers") or {}, rec.timeout_s
            )
            if not ok:
                raise BusinessError(BizCode.MODEL_NOT_READY, f"健康探测未通过，未启用：{message}")

        raw_meta = _loads(rec.meta_json)
        append_change_log(
            raw_meta, reason=body.change_reason, operator=body.operator,
            action="status", changed_fields=["status"],
        )
        await repo.update_fields(capability_id, {"status": body.status, "meta_json": raw_meta})
        count = await self._registry.refresh()
        return {
            "capability_id": capability_id,
            "status": body.status,
            "capabilities": count,
            "note": (
                "停用即从 ToolRegistry 卸载：在途任务下一次调用返回 tool not registered，"
                "且停用后健康巡检不再探测该行"
                if body.status != STATUS_ENABLED
                else "已启用并参与场景路由"
            ),
        }

    # ---------- 干跑（决策 34：不落库） ----------
    async def dry_run(self, body: DryRunBody) -> DryRunResult:
        """三段连通性。省略样例路径＝只跑 health 的连通性检查，不发放保存回执。"""
        steps: list[DryRunStep] = []
        headers = dict(body.headers or {})

        ok, message, latency = await self._probe_health(
            body.endpoint, _join(body.endpoint, body.health_path), headers, body.timeout_s
        )
        steps.append(DryRunStep(name="health", ok=ok, message=message, latency_ms=latency))

        sample: dict | None = None
        gate_errors: list[str] = []
        if ok:
            if (body.image_path or "").strip():
                ok2, message2, latency2, sample = await self._probe_inference(body, headers)
                steps.append(DryRunStep(name="inference", ok=ok2, message=message2, latency_ms=latency2))
                if sample is not None:
                    gate_errors = validate_response_sample(sample, body.capability_type)
                if ok2 and sample is not None and not gate_errors:
                    ok3, message3 = await self._probe_artifacts(sample, headers, body.timeout_s)
                    steps.append(DryRunStep(name="artifacts", ok=ok3, message=message3))
            else:
                steps.append(
                    DryRunStep(
                        name="inference", ok=True,
                        message="未填样例物料：本次仅为连通性检查，不产生保存回执",
                    )
                )
        else:
            steps.append(DryRunStep(name="inference", ok=False, message="健康探测未通过，跳过"))

        passed = all(s.ok for s in steps) and not gate_errors
        full_run = sample is not None and passed
        if full_run:
            snapshot = snapshot_of(body.endpoint, headers)
            issue_receipt(snapshot)
            return DryRunResult(
                passed=True, steps=steps, sample=sample, gate_errors=[], snapshot=snapshot
            )
        return DryRunResult(passed=passed, steps=steps, sample=sample, gate_errors=gate_errors, snapshot="")

    # ---------- 内部 ----------
    def _require_repo(self) -> CapabilityRepo:
        if self._repo is None:
            raise BusinessError(BizCode.INTERNAL_ERROR, "capability repo not configured")
        return self._repo

    def _require_dry_run_gate(self, endpoint: str, headers: Any, expected_snapshot: str | None) -> list[str]:
        """返回缺陷清单（空＝放行）：端点/请求头变更必须以一次成功的完整试运行回执为准。"""
        submitted = snapshot_of(endpoint, headers)
        if expected_snapshot != submitted:
            return [
                "端点或请求头已变更，必须先「完整试运行」并沿用其回执快照"
                f"（本次提交快照 {submitted}，收到的回执 {expected_snapshot or '无'}）"
            ]
        if not has_receipt(submitted):
            return [
                f"快照 {submitted} 没有对应的成功完整试运行记录"
                "（回执只在本进程内有效，过期或后端重启后需重跑「完整试运行」）"
            ]
        return []

    def _check_tool_name_unique(self, tool_name: str, exclude: str | None) -> None:
        """决策 35：禁止与「其他 enabled 行」同名（同 tool_name 的停用历史版本行允许共存）。"""
        for manifest in self._registry.list_enabled():
            if manifest.tool_name == tool_name and manifest.capability_id != exclude:
                raise BusinessError(
                    BizCode.INVALID_REQUEST,
                    f"tool_name={tool_name} 已被能力 {manifest.capability_id} 占用（停用旧行后方可复用）",
                )

    async def _probe_health(
        self, endpoint: str, health_url: str | None, headers: dict[str, str], timeout_s: int
    ) -> tuple[bool, str, int | None]:
        url = health_url or _join(endpoint, "/health")
        return await self._probe(url, headers, timeout_s)

    async def _probe(self, url: str, headers: dict[str, str], timeout_s: int) -> tuple[bool, str, int | None]:
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(float(timeout_s)), trust_env=False, headers=headers
            ) as client:
                resp = await client.get(resolve_base_url(url))
            latency = int((time.perf_counter() - started) * 1000)
            if resp.status_code >= 400:
                return False, f"HTTP {resp.status_code}: {resp.text[:200]}", latency
            body = _try_json(resp.text)
            code = body.get("code") if isinstance(body, dict) else None
            if code not in (None, 0):
                return False, f"业务码非 0：{code} {body.get('message', '')}"[:200], latency
            return True, f"HTTP {resp.status_code}，{latency}ms", latency
        except Exception as exc:  # noqa: BLE001 - 探测失败原因原样回传，不吞
            return False, f"{type(exc).__name__}: {exc}"[:200], None

    async def _probe_inference(
        self, body: DryRunBody, headers: dict[str, str]
    ) -> tuple[bool, str, int | None, dict | None]:
        paths = _resolve_sample_images(body.image_path)
        field = body.image_field or "image"
        files = [(field, (path.name, path.read_bytes(), _mime(path))) for path in paths]
        form = {
            "task_id": f"dryrun-{int(time.time() * 1000)}",
            "parameters": json.dumps(body.parameters or {}, ensure_ascii=False),
        }
        url = _join(body.endpoint, body.inference_path)
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(float(body.timeout_s)), trust_env=False, headers=headers
            ) as client:
                resp = await client.post(resolve_base_url(url), files=files, data=form)
            latency = int((time.perf_counter() - started) * 1000)
            if resp.status_code >= 400:
                return False, f"HTTP {resp.status_code}: {resp.text[:300]}", latency, None
            envelope = _try_json(resp.text)
            if not isinstance(envelope, dict):
                return False, "响应不是 JSON 对象", latency, None
            canonical = apply_tool_profile(envelope)
            return True, f"HTTP {resp.status_code}，{latency}ms", latency, canonical
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"[:200], None, None

    async def _probe_artifacts(
        self, sample: dict[str, Any], headers: dict[str, str], timeout_s: int
    ) -> tuple[bool, str]:
        artifacts = [a for a in (sample.get("artifacts") or []) if isinstance(a, dict) and a.get("uri")]
        if not artifacts:
            return True, "无 artifacts，跳过"
        checked = 0
        for art in artifacts[:5]:
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(float(timeout_s)), trust_env=False, headers=headers
                ) as client:
                    resp = await client.get(
                        resolve_base_url(str(art["uri"])), headers={"Range": "bytes=0-1023"}
                    )
                if resp.status_code not in (200, 206):
                    return False, f"{art.get('name')} 范围下载 HTTP {resp.status_code}"
                checked += 1
            except Exception as exc:  # noqa: BLE001
                return False, f"{art.get('name')} 下载失败：{type(exc).__name__}: {exc}"[:200]
        return True, f"{checked} 个产物范围 GET 通过"


def _check_scene(scene: str) -> None:
    if scene not in ALLOWED_SCENES:
        raise BusinessError(
            BizCode.INVALID_REQUEST,
            f"scene={scene} 不在路由白名单 {list(ALLOWED_SCENES)} 内：该能力永不被命中（死能力）",
        )


def _join(base: str, path: str) -> str:
    return (base or "").rstrip("/") + "/" + (path or "").lstrip("/")


def _mime(path: Path) -> str:
    return _MIME_BY_EXT.get(path.suffix.lower(), "application/octet-stream")


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
