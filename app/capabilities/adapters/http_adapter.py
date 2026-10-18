"""HTTP API 适配器：对齐《统一API 接入规范》§5.2 的 multipart 推理调用。

- 字段：image / task_id / parameters(JSON String) / engine_id / component_id / image_id / trace_id
- 重试：网络错误（超时/传输）与服务端 5xx（500/502/503/504）有限重试（指数退避）；
  4xx 类（参数/格式错误）立即上抛不重试
- 错误映射：工具返回的业务码 1001~4001 原样透传为平台 BusinessError
- 参数校验职责：平台仅负责把 parameters 序列化为 JSON String（非法 JSON 返回 1001）；
  取值范围/组合合法性由模型侧按填报表校验并返回 2001，平台不重复校验（避免双重 422 语义分歧）
- 超时：客户端级 120s 仅为兜底上限；逐调用超时以 AdapterContext.timeout_s 为准
  （manifest.timeout_s 经 InferTool 透传，invoke 显式传入）
- 健康检查：优先使用 manifest.health_endpoint（完整 URL 或 /path 形态），缺省 GET /health
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx

from app.models.capability import CapabilityManifest
from app.core.errors import BizCode, BusinessError
from app.capabilities.adapters.base import AdapterContext, RawToolResponse, ToolAdapter
from app.capabilities.adapters.tool_profiles import apply_tool_profile

_HTTP_TO_BIZ: dict[int, BizCode] = {
    400: BizCode.INVALID_REQUEST,
    413: BizCode.IMAGE_TOO_LARGE,
    415: BizCode.UNSUPPORTED_IMAGE_FORMAT,
    422: BizCode.INVALID_PARAMETER,
    500: BizCode.INFERENCE_FAILED,
    503: BizCode.MODEL_NOT_READY,
    504: BizCode.INFERENCE_TIMEOUT,
}

# 服务端瞬时故障状态码：有限重试（docstring 契约：网络错误 / 5xx 类）
_RETRYABLE_STATUS = {500, 502, 503, 504}

# 规范 §6：工具按扩展名 + Content-Type 双重校验图片（真实工具对
# application/octet-stream 返回 415/1002，mock 宽松）——按扩展名派生正确 MIME
_MIME_BY_EXT: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def resolve_base_url(base_url: str) -> str:
    """容错：宿主环境（如 OrbStack/摩卡容器隧道）可能劫持 localhost 解析为 IPv6，
    统一优先 127.0.0.1，保证 docker 内的 localhost 语义不被劫持。"""
    return base_url.replace("://localhost:", "://127.0.0.1:")


class HttpToolAdapter(ToolAdapter):
    """通用 HTTP 算法工具适配器（覆盖规范 §5.2 的"标准单图"与扩展多图形态）。"""

    def __init__(self, manifest: CapabilityManifest) -> None:
        self._manifest = manifest
        self.tool_name = manifest.tool_name
        self.scene = manifest.scene
        self._client = httpx.AsyncClient(
            base_url=resolve_base_url(manifest.endpoint),
            # 客户端级兜底上限；逐调用超时以 AdapterContext.timeout_s 为准（invoke 显式传入）
            timeout=httpx.Timeout(120.0),
            # 算法工具是本机/内网端点（127.0.0.1:181xx）：绕过 shell 代理环境变量，
            # 否则 HTTP_PROXY 会把工具调用送进代理 → ConnectError（同 RAGFlowClient）
            trust_env=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> tuple[bool, str]:
        health_endpoint = self._manifest.health_endpoint
        if health_endpoint:
            # 数据库/内置清单契约：health_endpoint 是完整 URL
            # （如 http://127.0.0.1:8002/health），httpx 绝对 URL 直接覆盖
            # base_url；纯路径形态（/healthz）则相对 base_url 请求
            if health_endpoint.startswith(("http://", "https://")):
                url: str = health_endpoint
            else:
                url = "/" + health_endpoint.lstrip("/")
        else:
            url = "/health"
        try:
            resp = await self._client.get(url)
            data = resp.json()
            # 兼容两种 /health 契约：规范形态 code==0，容器推理服务形态 status==healthy
            status = str(data.get("status", "")).lower()
            ok = resp.status_code == 200 and (data.get("code") == 0 or status in ("healthy", "ok"))
            return ok, data.get("message", "ok")
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    async def invoke(
        self,
        ctx: AdapterContext,
        image_paths: list[str],
        params: dict[str, Any],
        *,
        auxiliary: dict[str, str] | None = None,
    ) -> RawToolResponse:
        if not image_paths:
            raise BusinessError(BizCode.INVALID_REQUEST, "no image provided")

        field_name = self._manifest.input_required.get("image_field", "image")
        parameters = {
            **self._manifest.default_params,
            **(params or {}),
        }
        meta = {
            "task_id": ctx.task_id,
            "trace_id": ctx.trace_id,
            # 场景随调用下发：工具侧可据此做场景化默认（如金相缺陷回退）；
            # 对不识别该字段的真实工具为无害扩展（multipart 多余字段被忽略）
            "scene": self.scene,
            "engine_id": params.get("engine_id"),
            "component_id": params.get("component_id"),
            "image_id": params.get("image_id"),
        }

        # ---- 参数解析即平台层校验：非法 JSON 直接 1001（与 mock 行为一致）----
        if parameters:
            try:
                parameters_json = json.dumps(parameters, ensure_ascii=False)
            except (TypeError, ValueError) as exc:
                raise BusinessError(
                    BizCode.INVALID_REQUEST, f"parameters must be JSON: {exc}"
                ) from exc
            meta["parameters"] = parameters_json

        # 附加文件（红外辐射值 CSV、DR 参考图等）——同步读取移入工作线程，
        # 不占事件循环（LangGraph Studio 阻塞调用检测友好）
        def _read_files_sync() -> list[tuple[str, tuple[str, bytes, str]]]:
            collected: list[tuple[str, tuple[str, bytes, str]]] = []
            for path in image_paths:
                ext = os.path.splitext(path)[1].lower()
                mime = _MIME_BY_EXT.get(ext, "application/octet-stream")
                with open(path, "rb") as f:
                    collected.append((field_name, (path.split("/")[-1], f.read(), mime)))
            for name, path in (auxiliary or {}).items():
                with open(path, "rb") as f:
                    collected.append((name, (path.split("/")[-1], f.read(), "text/csv" if name.endswith("csv") else "application/octet-stream")))
            return collected

        files = await asyncio.to_thread(_read_files_sync)

        last_error: Exception | None = None
        attempts = ctx.retry_times + 1
        for attempt in range(attempts):
            try:
                # 逐调用超时采用治理预算（manifest.timeout_s 经 InferTool 透传），
                # 不得以客户端级硬编码超时替代
                resp = await self._client.post(
                    "/api/v1/inference",
                    data=meta,
                    files=files,
                    headers=(self._manifest.headers or {}),
                    timeout=ctx.timeout_s,
                )
                return self._parse_response(ctx, resp)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.5 * (2**attempt))
            except BusinessError as exc:
                # 服务端 5xx（网关/算法服务过载或重启中）属瞬时故障：有限重试；
                # 4xx 类（参数/格式错误）重试无意义，立即上抛
                if getattr(exc, "http_status", None) in _RETRYABLE_STATUS and attempt + 1 < attempts:
                    last_error = exc
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                raise

        raise BusinessError(
            BizCode.INFERENCE_TIMEOUT if isinstance(last_error, httpx.TimeoutException) else BizCode.ADAPTER_ERROR,
            f"tool {self.tool_name} unreachable: {last_error}",
            task_id=ctx.task_id,
        )

    def _parse_response(self, ctx: AdapterContext, resp: httpx.Response) -> RawToolResponse:
        try:
            data = resp.json()
        except Exception:
            raise BusinessError(
                BizCode.ADAPTER_ERROR,
                f"tool {self.tool_name} returned non-JSON (HTTP {resp.status_code})",
                task_id=ctx.task_id,
            ) from None

        # 交付工具外壳偏差归一（如 blade_defect_detector 的 data 外壳）：
        # 规范形态原样透传，仅偏差外壳被增补 canonical result/artifacts/performance
        data = apply_tool_profile(data)

        # 规范 §8：必须检查业务 code（HTTP 200 也可能带业务错误）
        biz_code = int(data.get("code", -1))
        if biz_code != 0:
            mapped = _HTTP_TO_BIZ.get(resp.status_code, BizCode.INFERENCE_FAILED)
            raise BusinessError(
                mapped,
                data.get("message") or f"tool error code={biz_code}",
                http_status=resp.status_code if resp.status_code >= 400 else None,
                task_id=ctx.task_id,
            )

        return RawToolResponse(
            ok=True,
            code=0,
            message=data.get("message", "success"),
            task_id=data.get("task_id"),
            trace_id=data.get("trace_id"),
            performance_ms=data.get("performance", {}).get("processing_time_ms"),
            payload=data,
        )