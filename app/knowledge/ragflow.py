"""RAGFlow 检索客户端（v0.15+，内置本项目实战踩坑处理）。

关键陷阱（memory/ragflow_api.md，均已内置处理）：
1. 删除无单文档路径：必须 DELETE /datasets/{id}/documents + body {"ids": [...]}
   且显式 Content-Type: application/json；HTTP 200 也可能携带业务错误，必须校验 body.code
2. 删除为异步：需轮询清单直到文档消失，否则重传同名会被自动重命名 xxx(1).pdf
3. retrieval 的 top_k 不可靠（传 5 可能回 30 条）：客户端必须自行截断
4. chunk 图像 image_id 不存在时返回 200+JSON，须同时校验状态码与 content-type
5. MinerU 解析器：parser_config.layout_recognize 必须用 env 完整三元组
   "mineru-from-env@mineru-from-env@MinerU"，且要 PATCH 每个 document（dataset 级不回退）
"""
from __future__ import annotations

import asyncio
import json
from app.core.logger import get_logger
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.errors import BizCode, BusinessError

logger = get_logger("app.knowledge.ragflow")

# host: model@instance@provider —— env provisioning 出的完整三元组，不能写字面量
LAYOUT_RECOGNIZE_TRIPLET = "mineru-from-env@mineru-from-env@MinerU"


class RAGFlowClient:
    """RAGFlow API 轻封装：数据集管理、文档解析（MinerU）、检索、重排、删除。"""

    def __init__(self, base_url: str, api_key: str) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(
            base_url=self._base,
            headers=self._headers,
            timeout=httpx.Timeout(60.0),
            # RAGFlow 是本机/内网服务：显式绕过 shell 的 HTTP(S)_PROXY 环境变量
            # （httpx 默认 trust_env=True 会把 localhost:9380 也送进代理，代理
            # no_proxy 未含 localhost 时启动即 ConnectError → chat 功能降级）
            trust_env=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def available(self) -> bool:
        return bool(self._headers.get("Authorization"))

    async def _check(self, resp: httpx.Response) -> dict[str, Any]:
        """业务码检查：HTTP 200 也可能携带错误（删除接口尤其如此）。"""
        try:
            data = resp.json()
        except Exception:
            raise BusinessError(
                BizCode.RAG_UNAVAILABLE, f"ragflow non-json: HTTP {resp.status_code}"
            ) from None
        if resp.status_code != 200 or int(data.get("code", -1)) != 0:
            raise BusinessError(
                BizCode.RAG_UNAVAILABLE,
                f"ragflow error: code={data.get('code')} message={data.get('message')}",
            )
        return data

    # ---------- 数据集 ----------
    async def list_dataset_ids_by_name(self, name: str) -> list[str]:
        """按名称过滤查数据集 id（?name= 过滤）。"""
        resp = await self._client.get("/api/v1/datasets", params={"name": name})
        data = await self._check(resp)
        return [ds["id"] for ds in data.get("data", [])]

    # ---------- 数据集清单（系统状态探测的数据源） ----------
    async def list_datasets(self) -> list[dict[str, Any]]:
        """全量数据集列表（不带 name 过滤）；不可达/认证失败经 _check 抛业务错。"""
        resp = await self._client.get("/api/v1/datasets")
        data = await self._check(resp)
        return data.get("data", [])

    # ---------- 服务端版本（监控页展示；成功后实例级缓存，版本不会运行中变化） ----------
    async def version(self) -> str | None:
        """GET /api/v1/system/version（v0.15+ 提供）；旧版无此路由/失败一律降级 None。"""
        cached = getattr(self, "_version", None)
        if cached is not None:
            return cached or None  # 空串＝探测过且不支持，不重复打
        try:
            resp = await self._client.get("/api/v1/system/version")
            if resp.status_code != 200:
                self._version = ""
                return None
            data = resp.json()
            if int(data.get("code", -1)) != 0:
                self._version = ""
                return None
            ver = str(data.get("data") or "").strip()
        except Exception:  # noqa: BLE001 版本探测永不阻断主流程
            self._version = ""
            return None
        self._version = ver
        return ver or None

    # ---------- Chat assistant / session ----------
    async def create_chat(self, config: dict[str, Any]) -> dict[str, Any]:
        resp = await self._client.post("/api/v1/chats", json=config)
        data = await self._check(resp)
        return data.get("data", {})

    async def list_chats(self, *, name: str | None = None) -> list[dict[str, Any]]:
        params = {"name": name} if name else None
        resp = await self._client.get("/api/v1/chats", params=params)
        data = await self._check(resp)
        payload = data.get("data", [])
        if isinstance(payload, dict):
            payload = payload.get("chats", [])
        return payload if isinstance(payload, list) else []

    async def update_chat(self, chat_id: str, config: dict[str, Any]) -> dict[str, Any]:
        resp = await self._client.patch(f"/api/v1/chats/{chat_id}", json=config)
        data = await self._check(resp)
        return data.get("data", {})

    async def create_chat_session(self, chat_id: str, name: str) -> dict[str, Any]:
        resp = await self._client.post(
            f"/api/v1/chats/{chat_id}/sessions", json={"name": name}
        )
        data = await self._check(resp)
        return data.get("data", {})

    async def list_chat_sessions(self, chat_id: str) -> list[dict[str, Any]]:
        resp = await self._client.get(
            f"/api/v1/chats/{chat_id}/sessions",
            params={"page": 1, "page_size": 100, "orderby": "update_time", "desc": "true"},
        )
        data = await self._check(resp)
        payload = data.get("data", [])
        return payload if isinstance(payload, list) else []

    async def get_chat_session(self, chat_id: str, session_id: str) -> dict[str, Any]:
        resp = await self._client.get(f"/api/v1/chats/{chat_id}/sessions/{session_id}")
        data = await self._check(resp)
        return data.get("data", {})

    async def update_chat_session(
        self, chat_id: str, session_id: str, *, name: str
    ) -> dict[str, Any]:
        """更新会话名称；消息与引用仍由 RAGFlow 管理，不允许经此入口修改。"""
        resp = await self._client.patch(
            f"/api/v1/chats/{chat_id}/sessions/{session_id}",
            json={"name": name},
        )
        data = await self._check(resp)
        return data.get("data", {})

    async def delete_chat_session(self, chat_id: str, session_id: str) -> None:
        resp = await self._client.request(
            "DELETE",
            f"/api/v1/chats/{chat_id}/sessions",
            content=json.dumps({"ids": [session_id]}),
        )
        await self._check(resp)

    async def delete_chat_message(
        self, chat_id: str, session_id: str, message_id: str
    ) -> dict[str, Any]:
        resp = await self._client.delete(
            f"/api/v1/chats/{chat_id}/sessions/{session_id}/messages/{message_id}"
        )
        data = await self._check(resp)
        return data.get("data", {})

    async def stream_chat_completion(
        self,
        chat_id: str,
        session_id: str,
        question: str,
        message_id: str,
    ) -> AsyncIterator[bytes]:
        """透传 RAGFlow 原生 SSE；调用方断开时 async context 会关闭上游连接。"""
        payload = {
            "chat_id": chat_id,
            "session_id": session_id,
            "stream": True,
            "legacy": False,
            "messages": [{"role": "user", "content": question, "id": message_id}],
        }
        async with self._client.stream(
            "POST", "/api/v1/chat/completions", json=payload
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", errors="replace")[:500]
                raise BusinessError(
                    BizCode.RAG_UNAVAILABLE,
                    f"ragflow chat error: HTTP {resp.status_code} {body}",
                )
            async for chunk in resp.aiter_bytes():
                if chunk:
                    yield chunk

    # ---------- 文档上传与解析 ----------
    async def upload_document(self, dataset_id: str, file_path: str) -> dict[str, Any]:
        """上传文档（multipart），一个文件对应一个 doc。"""
        with open(file_path, "rb") as f:
            files = {"file": (file_path.split("/")[-1], f, "application/octet-stream")}
            resp = await self._client.post(
                f"/api/v1/datasets/{dataset_id}/documents", files=files
            )
        data = await self._check(resp)
        return data.get("data", [{}])[0]

    async def upload_documents(
        self, dataset_id: str, items: list[tuple[str, bytes]]
    ) -> list[dict[str, Any]]:
        """批量上传文档（multipart，items=[(文件名, 字节)]）。

        端点层收到的是内存 UploadFile，无磁盘路径，故走字节分支；
        避开按路径 split("/") 取名的 Windows 反斜杠陷阱（文件名直接来自上传头）。
        """
        files = [("file", (name, data, "application/octet-stream")) for name, data in items]
        resp = await self._client.post(
            f"/api/v1/datasets/{dataset_id}/documents", files=files
        )
        payload = (await self._check(resp)).get("data", [])
        if isinstance(payload, dict):
            payload = payload.get("docs") or [payload]
        return payload if isinstance(payload, list) else []

    async def set_mineru_layout(self, dataset_id: str, document_ids: list[str]) -> None:
        """强制文档使用 MinerU 解析（env 注入 ≠ 已启用，必须 PATCH 每个 document）。"""
        for doc_id in document_ids:
            resp = await self._client.patch(
                f"/api/v1/datasets/{dataset_id}/documents",
                json={"ids": [doc_id], "parser_config": {"layout_recognize": LAYOUT_RECOGNIZE_TRIPLET}},
            )
            await self._check(resp)

    async def trigger_chunks(self, dataset_id: str, document_ids: list[str]) -> None:
        """手工触发切块解析（chunk_method=naive 时）。"""
        resp = await self._client.post(
            f"/api/v1/datasets/{dataset_id}/chunks",
            json={"document_ids": document_ids},
        )
        await self._check(resp)

    async def wait_parsed(self, dataset_id: str, document_ids: list[str], timeout_s: float = 300.0) -> None:
        """轮询 run 字段直至 DONE/FAIL。"""
        deadline = time.monotonic() + timeout_s
        pending = set(document_ids)
        while pending and time.monotonic() < deadline:
            docs = await self.list_documents(dataset_id)
            for doc in docs:
                if doc.get("id") in pending and doc.get("run") in ("DONE", "FAIL"):
                    pending.discard(doc["id"])
            if pending:
                await asyncio.sleep(2.0)
        if pending:
            logger.warning("documents not parsed in time: %s", sorted(pending))

    # ---------- 检索与重排 ----------
    async def retrieve(
        self,
        question: str,
        dataset_ids: list[str],
        top_k: int = 5,
        similarity_threshold: float = 0.2,
        retrieval_method: str = "hybrid",
        document_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """混合检索 + 客户端截断（RAGFlow page_size 不可靠）。

        document_ids：库内文档前置过滤（服务端已实测支持）——场景文档一致性
        路由用：只在允许清单内做向量/全文排序，top_k 名额不被跨场景文档挤占。
        """
        payload: dict[str, Any] = {
            "question": question,
            "dataset_ids": dataset_ids,
            "page": 1,
            "page_size": max(top_k * 2, 30),
            "similarity_threshold": similarity_threshold,
            "retrieval_method": retrieval_method,
        }
        if document_ids:
            payload["document_ids"] = list(document_ids)
        # 三种模式对应各自权重参数（实测契约点 1 确认的枚举名，若偏离以 curl 结果为准）
        if retrieval_method == "vector":
            payload["vector_similarity_weight"] = 0.3
        elif retrieval_method == "fulltext":
            payload["keyword_similarity_weight"] = 0.3
        resp = await self._client.post("/api/v1/retrieval", json=payload)
        data = await self._check(resp)
        # 按相关度降序后截断：top_k 语义 = 分值最高的前 k 条（测试规格耦合；真实服务已降序，排序幂等）
        items = sorted(
            data.get("data", {}).get("chunks", []),
            key=lambda x: x.get("similarity", 0.0),
            reverse=True,
        )
        chunks = []
        for item in items:
            if len(chunks) >= top_k:
                break
            chunks.append({
                "content": item.get("content", ""),
                "score": item.get("similarity", 0.0),
                "doc_name": item.get("document_keyword", item.get("document_name", "")),
                # 契约实测：chunk 标识字段名为 id（nightly 无 chunk_id）→ 双兼容归一化
                "chunk_id": item.get("chunk_id") or item.get("id"),
                "image_id": item.get("image_id"),
                "document_id": item.get("document_id"),
                # 页码透传（可能缺失 → None）：报告引用需定位到来源页（需求 §5.3 引用溯源）
                "page": item.get("page"),
            })
        return chunks

    async def rerank(
        self, query: str, chunks: list[dict[str, Any]], model: str
    ) -> list[dict[str, Any]]:
        """重排序（需 ragflow 侧配置 rerank_model；失败降级返回原序）。"""
        payload = {
            "query": query,
            "documents": [{"content": c["content"]} for c in chunks],
            "rerank_model": model,
        }
        try:
            resp = await self._client.post("/api/v1/rerank", json=payload)
            data = await self._check(resp)
            scores = data.get("data", [])
            ranked = [
                {**chunks[i], "rank": s.get("score", 0.0)}
                for i, s in enumerate(scores)
                if i < len(chunks)
            ]
            return sorted(ranked, key=lambda x: x["rank"], reverse=True)
        except Exception:
            logger.warning("rerank failed, degrade to original order", exc_info=True)
            return [{**c, "rank": 0.0} for c in chunks]

    # ---------- 删除（最容易踩坑：ids body + Content-Type 显式指定） ----------
    async def delete_documents(self, dataset_id: str, document_ids: list[str]) -> None:
        if not document_ids:
            return
        resp = await self._client.request(
            "DELETE",
            f"/api/v1/datasets/{dataset_id}/documents",
            content=json.dumps({"ids": document_ids}),
        )
        await self._check(resp)

    # 既有 list_documents 按契约实测修正：data 为 {docs, total} dict（2026-08-19 披露升级为修复，
    # wait_documents_gone 遍历依赖其输出为 list，dict 形态返回 dict 会致其失效）
    async def list_documents(self, dataset_id: str) -> list[dict[str, Any]]:
        resp = await self._client.get(f"/api/v1/datasets/{dataset_id}/documents")
        data = await self._check(resp)
        payload = data.get("data", [])
        if isinstance(payload, dict):
            payload = payload.get("docs", [])
        return payload

    async def list_all_documents(
        self, dataset_id: str, *, page_size: int = 100, max_pages: int = 50
    ) -> list[dict[str, Any]]:
        """全量文档清单（分页遍历；list_documents 单页默认只有 30 条）。

        场景文档路由（scene_doc_excludes）的数据源：需要看到库内全部文档名
        才能算出场景允许清单；max_pages 防御异常 total 导致的死循环。
        """
        out: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            resp = await self._client.get(
                f"/api/v1/datasets/{dataset_id}/documents",
                params={"page": page, "page_size": page_size},
            )
            data = await self._check(resp)
            payload = data.get("data") or {}
            docs = payload.get("docs", []) if isinstance(payload, dict) else payload
            out.extend(docs)
            total = payload.get("total") if isinstance(payload, dict) else None
            if not docs or (total is not None and len(out) >= int(total)):
                break
        return out

    async def wait_documents_gone(
        self, dataset_id: str, document_ids: list[str], timeout_s: float = 60.0
    ) -> bool:
        """删除为异步：轮询清单直到全部消失（实测 10 份约 10 秒收敛）。"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            docs = await self.list_documents(dataset_id)
            ids = {d.get("id") for d in docs}
            if not (ids & set(document_ids)):
                return True
            await asyncio.sleep(2.0)
        return False

    def image_uri(self, image_id: str) -> str:
        """chunk 图像直链（前端经平台代理展示，避免密钥下发）。
        前缀必须命中 app/api/v1/ragflow.py 的 /images/{image_id} 代理路由；
        历史前缀 /api/kb/images/ 无对应路由，会落 SPA fallback 返回 HTML。"""
        return f"/api/v1/ragflow/images/{image_id}"

    # ---------- 分块 / 下载 / 图片（Task 1 扩展，Task 2/3 端点复用） ----------
    async def list_document_chunks(self, dataset_id: str, document_id: str, page_size: int = 100) -> list[dict[str, Any]]:
        """取文档全量 chunks（chunk_id/content/image_id），满页续翻直到不足一页或空页。

        契约实测（2026-08-19）：RAGFlow list-chunks 服务端默认 page_size=30 且上限 100
        （page_size>100 抛 ValueError），GH738 文档 54 chunks 默认只回前 30，
        检索按相关性全局命中的 chunk 落在第 1 页外取不到
        → 显式 page_size=100 + 续翻，保证第 1 页外的命中段也进预览。
        """
        all_chunks: list[dict[str, Any]] = []
        gathered = 0
        while True:
            resp = await self._client.get(
                f"/api/v1/datasets/{dataset_id}/documents/{document_id}/chunks",
                params={"page": gathered // page_size + 1, "page_size": page_size},
            )
            data = await self._check(resp)
            chunks = data.get("data", {})
            if isinstance(chunks, dict):
                chunks = chunks.get("chunks", [])
            if not chunks:
                break
            all_chunks.extend(chunks)
            gathered += len(chunks)
            if len(chunks) < page_size:
                break
        # 契约实测：chunk 标识字段名为 id → 归一化为 chunk_id（端点层与前端只认 chunk_id）
        # page 透传（可能缺失 → None）：引用原文预览展示命中块所在页
        return [{"chunk_id": c.get("chunk_id") or c.get("id"),
                 "content": c.get("content", ""),
                 "image_id": c.get("image_id"),
                 "page": c.get("page")} for c in all_chunks]

    async def download_document(self, document_id: str) -> httpx.Response:
        """原始二进制响应，不经 _check（内容校验/错误语义由端点层处理）。

        契约实测（2026-08-19）：/datasets/{ds}/documents/{doc}/file 与 /content 均 404，
        下载唯一可用路径为 GET /api/v1/documents/{document_id}（带 Content-Disposition 附件头）。
        """
        return await self._client.get(f"/api/v1/documents/{document_id}")

    async def get_image(self, image_id: str) -> httpx.Response:
        """原始响应，不经 _check（可能存在 200+JSON 陷阱，端点层做双校验）。"""
        return await self._client.get(f"/api/v1/documents/images/{image_id}")
