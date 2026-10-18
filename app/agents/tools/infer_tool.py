"""算法推理工具：以 manifest 驱动的一次算法调用（孔探/红外/断口等检测是同一实现）。

每个 CapabilityManifest 注册为一个独立 Tool 实例（name = detect/<tool_name>），
LangGraph 的 infer 节点按 capability.tool_name 从注册表解析。

**动态解析（纯 DB 驱动）**：Tool 构造时只记录 capability_id，execute 时从能力注册中心
按 capability_id 实时取最新 manifest——DB 侧启停/参数经 15s 轮询刷新后，本工具自动读到
新值，无需重启 / 无需重建 ToolRegistry（新增算法 tool name 才需 reload 重建装配）。
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from app.agents.tools.registry import Tool, ToolResult
from app.models.capability import CapabilityManifest
from app.capabilities.registry import CapabilityRegistry
from app.core.errors import BizCode, BusinessError
from app.capabilities.adapters.base import AdapterContext, AdapterPool
from app.capabilities.adapters.http_adapter import resolve_base_url

# Content-Type → file_repo 安全扩展名（真实工具产物 URI 末段常无扩展名，
# 如 /api/v1/detections/{rid}/original；白名单外扩展名会被 save_bytes 拒绝）
_EXT_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/tiff": ".tif",
    "application/json": ".json",
    "application/pdf": ".pdf",
    "text/csv": ".csv",
    "text/markdown": ".md",
}


def _artifact_filename(uri: str, resp: httpx.Response) -> str:
    """从 URI path 末段取文件名；无扩展名时按响应 Content-Type 补全。"""
    name = urlsplit(uri).path.rsplit("/", 1)[-1] or "artifact"
    if "." not in name:
        mime = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        name += _EXT_BY_MIME.get(mime, ".bin")
    return name


class InferTool(Tool[ToolResult]):
    """算法检测工具：适配器调用 + 业务码映射 + 标注图本地化。

    治理接线：构造时可注入共享 AdapterPool（per-tool 信号量 + 熔断器，
    容器按 gateway_concurrency / gateway_breaker_threshold 装配）；
    未注入（默认）则不加治理，保持单测/脚本的最小依赖。
    """

    def __init__(
        self,
        manifest: CapabilityManifest,
        capability_registry: CapabilityRegistry,
        pool: AdapterPool | None = None,
    ) -> None:
        # 只记录稳定身份；execute 时动态解析最新 manifest（支持 DB 热更）
        self.name = f"detect/{manifest.tool_name}"
        self.description = f"{manifest.description or manifest.tool_name}（{manifest.tool_version}）场景 {manifest.scene}"
        self.scene = manifest.scene
        self._capability_id = manifest.capability_id
        self._registry = capability_registry
        self._pool = pool

    def _manifest(self) -> CapabilityManifest | None:
        """取当前能力最新 manifest（某次 15s 轮询刷新后自动拿到新值）。"""
        if self._registry is None:
            return None
        return self._registry.get(self._capability_id) or self._registry.get_by_tool_name(
            self.name.rsplit("/", 1)[-1]
        )

    async def execute(self, inputs: dict[str, Any]) -> ToolResult:
        """inputs: {task_id, trace_id, image_paths, auxiliary_paths, parameters}"""
        manifest = self._manifest()
        if manifest is None:
            return ToolResult.failure(
                int(BizCode.INTERNAL_ERROR), f"capability {self._capability_id} unavailable"
            )

        adapter = await self._registry.adapter_for(self._capability_id)
        adapter_ctx = AdapterContext(
            task_id=inputs["task_id"],
            trace_id=inputs.get("trace_id", ""),
            timeout_s=manifest.timeout_s or 60.0,
            retry_times=max(manifest.max_retry, 1),
        )
        params = {
            **(manifest.default_params or {}),
            **(inputs.get("parameters") or {}),
        }
        image_paths = inputs.get("image_paths") or []
        auxiliary = inputs.get("auxiliary_paths") or {}

        # ---- 多图 fan-out（2026-09-16 生产事故）：真实工具为单图契约
        # （manifest input_required.images=1），多图塞进同一次请求会被工具
        # 静默丢弃（只检第一张）。单图契约 + 多图输入 -> 平台逐图调用并合并，
        # 工具侧契约不变；任一图失败整体失败（与 mock「任一图非法即拒绝」
        # 语义一致，绝不静默丢图）。
        declared = (manifest.input_required or {}).get("images") or 0
        try:
            declared_n = int(declared)
        except (TypeError, ValueError):
            declared_n = 0
        if declared_n == 1 and len(image_paths) > 1:
            return await self._execute_fanout(
                manifest, adapter, adapter_ctx, inputs, image_paths, params, auxiliary
            )

        try:
            response = await self._governed_invoke(
                manifest, adapter, adapter_ctx, image_paths, params, auxiliary
            )
        except BusinessError as exc:
            # 业务码保真：适配器抛出的 BusinessError 携带规范业务码与消息
            # （网关错误码透传契约），不再压平为 3002
            return ToolResult.failure(int(exc.code), exc.message or "tool failed")
        except httpx.HTTPError as exc:
            return ToolResult.failure(int(BizCode.INFERENCE_FAILED), f"http error: {exc}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(int(BizCode.INFERENCE_FAILED), f"adapter error: {exc}")

        payload: dict[str, Any] = response.payload or {}
        if not response.ok or payload.get("code", -1) != 0:
            return ToolResult.failure(
                int(_map_tool_biz(payload.get("code"))),
                payload.get("message") or "tool returned failure",
            )

        raw = await self._finalize_payload(payload, inputs)
        return ToolResult.success({"raw_result": raw})

    async def _governed_invoke(
        self,
        manifest: CapabilityManifest,
        adapter: Any,
        adapter_ctx: AdapterContext,
        image_paths: list[str],
        params: dict[str, Any],
        auxiliary: dict[str, str],
    ):
        """治理门内的一次适配器调用：熔断 acquire + 信号量限流 + 成败上报。"""
        # 治理门（可选）：熔断打开 -> BusinessError 快速失败；
        # 信号量限流并发；无论成败都向熔断器报告（成败是下一次调用的依据）
        if self._pool is not None:
            await self._pool.breaker(manifest.tool_name).acquire()
            sem = self._pool.semaphore(manifest.tool_name)
        else:
            sem = None

        try:
            ok = False
            if sem is not None:
                async with sem:
                    response = await adapter.invoke(
                        adapter_ctx, image_paths, params, auxiliary=auxiliary
                    )
            else:
                response = await adapter.invoke(
                    adapter_ctx, image_paths, params, auxiliary=auxiliary
                )
            ok = bool(response.ok)
            return response
        finally:
            if self._pool is not None:
                await self._pool.breaker(manifest.tool_name).report(ok)

    async def _execute_fanout(
        self,
        manifest: CapabilityManifest,
        adapter: Any,
        adapter_ctx: AdapterContext,
        inputs: dict[str, Any],
        image_paths: list[str],
        params: dict[str, Any],
        auxiliary: dict[str, str],
    ) -> ToolResult:
        """单图契约工具的多图 fan-out：逐图调用（治理每图计数）+ 结果合并。

        合并规则（平台 canonical，报告/判级消费）：
        - defects 拼接并标注来源图（image_name + description 前缀），全局连号
        - status 任一 abnormal 即 abnormal；summary 汇总 N/M 张图异常
        - 数值业务字段求和（false_indication_count 等）
        - artifacts 拼接，name 加来源图名后缀（报告逐图配对 + 落盘名不冲突）
        """
        from pathlib import Path

        payloads: list[tuple[str, dict[str, Any]]] = []
        for path in image_paths:
            image_name = Path(path).name
            try:
                response = await self._governed_invoke(
                    manifest, adapter, adapter_ctx, [path], params, auxiliary
                )
            except BusinessError as exc:
                return ToolResult.failure(
                    int(exc.code), f"{image_name}: {exc.message or 'tool failed'}"
                )
            except httpx.HTTPError as exc:
                return ToolResult.failure(
                    int(BizCode.INFERENCE_FAILED), f"{image_name}: http error: {exc}"
                )
            except Exception as exc:  # noqa: BLE001
                return ToolResult.failure(
                    int(BizCode.INFERENCE_FAILED), f"{image_name}: adapter error: {exc}"
                )
            payload: dict[str, Any] = response.payload or {}
            if not response.ok or payload.get("code", -1) != 0:
                # 任一图失败整体失败（带图名定位），绝不静默丢图
                return ToolResult.failure(
                    int(_map_tool_biz(payload.get("code"))),
                    f"{image_name}: {payload.get('message') or 'tool returned failure'}",
                )
            payloads.append((image_name, payload))

        merged = _merge_fanout_payloads(payloads)
        raw = await self._finalize_payload(merged, inputs)
        return ToolResult.success({"raw_result": raw})

    async def _finalize_payload(
        self, payload: dict[str, Any], inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """payload 收尾：artifacts 本地化（name 可含 fan-out 图名后缀）+ task_id。"""
        # artifacts 本地化（标注图等，URI 供前端/报告引用）——随工具一并完成。
        # 两种形态：mock 相对路径 /artifacts/…；真实工具绝对 URI（可带签名 query，
        # 如 blade_defect_detector 的 http://…/api/v1/detections/{rid}/original）
        file_repo = inputs.get("file_repo")
        artifact_info: list[dict[str, Any]] = []
        for art in payload.get("artifacts") or []:
            uri = art.get("uri", "")
            if not uri or not (
                uri.startswith("/artifacts/") or uri.startswith(("http://", "https://"))
            ):
                continue
            # fan-out 合并的产物 name 已带图名后缀（__<stem>），落盘名与展示名
            # 同步携带，避免多图同名产物互相覆盖/无法逐图配对
            path = await self._localize_artifact(uri, inputs["task_id"], file_repo,
                                                 name_hint=art.get("name", ""))
            if path:
                artifact_info.append(
                    {
                        "name": f"artifact_{art.get('name', 'file')}",
                        "mime": art.get("mime_type"),
                        "uri": path,
                    }
                )
        return {**payload, "task_id": inputs["task_id"], "artifacts": artifact_info}

    async def _localize_artifact(
        self, uri: str, task_id: str, file_repo, name_hint: str = ""  # noqa: ANN001
    ) -> str | None:
        """下载产物到本地文件仓储，返回业务 URI（失败不阻断主流程）。

        相对 /artifacts/… 走能力 endpoint base_url；绝对 http(s) URI 直连
        （真实工具产物带主机与签名 query）。落盘文件名无安全扩展名时按
        Content-Type 补全（file_repo 扩展名白名单）。name_hint 为 fan-out
        图名后缀（__<stem>）：同名产物多图落盘不互相覆盖。
        """
        if file_repo is None:
            return None
        try:
            if uri.startswith(("http://", "https://")):
                # 产物主机 = 工具/MinIO 本机内网端点：绕过 shell 代理（同 http_adapter）
                async with httpx.AsyncClient(
                    timeout=30.0, follow_redirects=True, trust_env=False
                ) as client:
                    resp = await client.get(uri)
            else:
                manifest = self._manifest()
                if manifest is None:
                    return None
                async with httpx.AsyncClient(
                    base_url=resolve_base_url(manifest.endpoint), timeout=30.0, trust_env=False
                ) as client:
                    resp = await client.get(uri)
            if resp.status_code != 200:
                return None
            filename = _artifact_filename(uri, resp)
            if name_hint and "__" in name_hint:
                # fan-out 图名后缀：插到扩展名前（result_image__img1.jpg），
                # 多图同名产物不互相覆盖
                stem, dot, ext = filename.rpartition(".")
                if dot:
                    filename = f"{stem}__{name_hint.rsplit('__', 1)[-1]}.{ext}"
                else:
                    filename = f"{filename}__{name_hint.rsplit('__', 1)[-1]}"
            return file_repo.save_bytes(task_id, filename, resp.content, kind="artifacts")
        except (httpx.HTTPError, ValueError, OSError, AttributeError):
            return None


def _merge_fanout_payloads(payloads: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """合并单图契约工具的逐图响应为平台 canonical 多图结果。

    payloads: [(image_name, payload)]——payload 为工具响应（code==0 已校验）。
    合并产物供 normalize_result/判级/报告消费：defects 带 image_name 溯源，
    artifacts 带 __<stem> 图名后缀（报告逐图配对 + 落盘不冲突）。
    """
    from pathlib import Path

    first = payloads[0][1]
    merged: dict[str, Any] = dict(first)  # tool/meta 等信封字段取首个
    first_result = first.get("result") or {}
    if not isinstance(first_result, dict):
        first_result = {}

    all_defects: list[dict[str, Any]] = []
    abnormal_images = 0
    false_count = 0
    artifacts: list[dict[str, Any]] = []
    perf_ms = 0

    for image_name, payload in payloads:
        result = payload.get("result") or {}
        if not isinstance(result, dict):
            result = {}
        defects = result.get("defects") or []
        if result.get("status") == "abnormal":
            abnormal_images += 1
        # 数值业务字段求和（虚假显示计数等；非数值容错跳过）
        fc = result.get("false_indication_count")
        if isinstance(fc, (int, float)):
            false_count += fc
        for det in defects:
            if not isinstance(det, dict):
                continue
            entry = dict(det)
            entry["image_name"] = image_name
            desc = str(entry.get("description") or "")
            if not desc.startswith(f"{image_name}："):
                entry["description"] = f"{image_name}：{desc}" if desc else image_name
            all_defects.append(entry)
        # 产物拼接 + 图名后缀（stem 去扩展名，避免超长/特殊字符）
        stem = Path(image_name).stem
        for art in payload.get("artifacts") or []:
            if not isinstance(art, dict) or not art.get("uri"):
                continue
            merged_art = dict(art)
            name = str(merged_art.get("name") or "file")
            if f"__{stem}" not in name:
                merged_art["name"] = f"{name}__{stem}"
            artifacts.append(merged_art)
        perf = payload.get("performance") or {}
        if isinstance(perf, dict) and isinstance(perf.get("processing_time_ms"), (int, float)):
            perf_ms += perf["processing_time_ms"]

    # defect_id 全局连号（跨图唯一，复核 diff 按 id 对齐）
    for index, entry in enumerate(all_defects):
        entry["defect_id"] = f"D{index + 1:03d}"

    if all_defects:
        status = "abnormal"
        summary = (
            f"检测到{len(all_defects)}处疑似缺陷"
            f"（{abnormal_images}/{len(payloads)}张图异常）"
        )
    else:
        status = "normal"
        summary = "未发现明显异常"

    merged_result = dict(first_result)
    merged_result.update(
        {
            "status": status,
            "summary": summary,
            "defects": all_defects,
            "false_indication_count": false_count or first_result.get("false_indication_count"),
            "image_count": len(payloads),
            "image_name": payloads[0][0],
        }
    )
    merged["result"] = merged_result
    if artifacts:
        merged["artifacts"] = artifacts
    if "performance" in merged or perf_ms:
        merged["performance"] = {"processing_time_ms": perf_ms or None}
    return merged


def _map_tool_biz(code: Any) -> BizCode:
    table: dict[int, BizCode] = {
        1001: BizCode.INVALID_REQUEST,
        1002: BizCode.UNSUPPORTED_IMAGE_FORMAT,
        1003: BizCode.IMAGE_TOO_LARGE,
        1004: BizCode.INVALID_IMAGE,
        2001: BizCode.INVALID_PARAMETER,
        3001: BizCode.MODEL_NOT_READY,
        3002: BizCode.INFERENCE_FAILED,
        3003: BizCode.INFERENCE_TIMEOUT,
        4001: BizCode.INTERNAL_ERROR,
    }
    try:
        return table.get(int(code), BizCode.INFERENCE_FAILED)
    except (TypeError, ValueError):
        return BizCode.INFERENCE_FAILED