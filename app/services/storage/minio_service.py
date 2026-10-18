"""MinIO 对象存储服务：内容寻址去重（秒传）+ 元数据落库。

核心语义（对齐指令）：
1. 接收文件流后**分块读取**（Chunked Reading，1 MiB/块）实时计算 SHA-256；
2. 查询 file_objects 表：Hash 已存在 → 直接复用已有 URI（秒传，仅引用计数 +1）；
3. Hash 不存在 → 以 {sha256}.{ext} 为 Object Name 上传至专用桶，
   再把 URI / 大小 / 类型等元数据落库；
4. 对象名天然防碰撞（内容寻址），相同文件无论重复上传多少次只存一份。

部署注意：endpoint 未配置时 StorageService 整体降级为 None（沿用本地落盘路径），
保证无 MinIO 环境（开发/测试）链路不依赖对象存储。
"""
from __future__ import annotations

import hashlib
import io

import uuid
from pathlib import Path
from typing import BinaryIO, Any

from minio import Minio

from app.core.logger import get_logger
from app.repositories.object_repo import ObjectRepo

logger = get_logger("app.storage.minio")

# 分块大小：1 MiB（S3 multipart 语义下限的一半，兼顾吞吐与内存）
CHUNK_SIZE = 1024 * 1024
DEFAULT_BUCKET = "qc-media-assets"

# 扩展名 -> MIME（与路由层 ALLOWED_IMAGE_EXTS 对齐；未知扩展回退 octet-stream）
_EXT_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".csv": "text/csv",
    ".json": "application/json",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
}


class StorageService:
    """MinIO 封装门面：桶管理 / 去重上传 / 查询 / 预签名 / 下载。"""

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str = DEFAULT_BUCKET,
        secure: bool = False,
        object_repo: ObjectRepo,
    ) -> None:
        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        self._bucket = bucket
        self._repo = object_repo

    # ---------- 桶管理 ----------
    def ensure_bucket(self) -> None:
        """自动创建专用存储桶（幂等），不存在才建。"""
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)
            logger.info("minio bucket created: %s", self._bucket)

    @property
    def bucket(self) -> str:
        return self._bucket

    # ---------- 内容寻址去重上传 ----------
    async def store_stream(
        self,
        stream: BinaryIO,
        *,
        filename: str,
        task_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """分块读取流 -> SHA-256 -> 秒传复用 or 上传{sha256}.{ext} -> 返回引用信息。

        返回: {sha256, uri, object_name, size_bytes, content_type, deduped}
          deduped=True 表示命中已有 Hash，未发生重复上传（秒传）。
        """
        digest, content = await self._hash_stream(stream)
        ext = Path(filename).suffix.lower()
        content_type = _EXT_MIME.get(ext, "application/octet-stream")

        # 秒传短路：Hash 已在库 -> 直接复用（不重复上传）
        existing = await self._repo.get_by_sha256(digest)
        if existing is not None:
            await self._repo.bump_ref(digest, task_id)
            logger.info("dedupe hit sha256=%s object=%s", digest, existing.object_name)
            return {
                "sha256": digest,
                "uri": existing.uri,
                "object_name": existing.object_name,
                "size_bytes": existing.size_bytes or len(content),
                "content_type": content_type,
                "deduped": True,
            }

        object_name = f"{digest}{ext}"
        await self._upload_bytes(object_name, content, content_type)
        uri = self.uri_for(object_name)
        created = await self._repo.upsert(
            sha256=digest,
            bucket=self._bucket,
            object_name=object_name,
            uri=uri,
            ext=ext.lstrip(".") or None,
            size_bytes=len(content),
            content_type=content_type,
            task_id=task_id,
            meta=meta,
        )
        logger.info(
            "object stored sha256=%s object=%s created=%s", digest, object_name, created
        )
        return {
            "sha256": digest,
            "uri": uri,
            "object_name": object_name,
            "size_bytes": len(content),
            "content_type": content_type,
            "deduped": False,
        }

    async def store_bytes(
        self,
        content: bytes,
        *,
        filename: str,
        task_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """内存全量字节上传（UploadFile 小对象便捷路径，内部仍分块算 Hash）。"""
        return await self.store_stream(io.BytesIO(content), filename=filename, task_id=task_id, meta=meta)

    # ---------- 查询 / 访问 ----------
    @staticmethod
    def uri_for(object_name: str, bucket: str = DEFAULT_BUCKET) -> str:
        return f"minio://{bucket}/{object_name}"

    def presigned_url(self, object_name: str, expires_s: int = 3600) -> str:
        """临时只读 URL（前端/报告引用图片）。"""
        return self._client.presigned_get_object(self._bucket, object_name, expires=expires_s)

    async def download_to(self, object_name: str, dest: Path) -> Path:
        """拉取对象到本地路径（工具 multipart 需要真实文件时使用）。"""
        dest.parent.mkdir(parents=True, exist_ok=True)
        resp = self._client.get_object(self._bucket, object_name)
        try:
            with open(dest, "wb") as f:
                for chunk in resp.stream(CHUNK_SIZE):
                    f.write(chunk)
        finally:
            resp.close()
            resp.release_conn()
        return dest

    # ---------- 内部 ----------
    async def _hash_stream(self, stream: BinaryIO) -> tuple[str, bytes]:
        """分块读取：边算 SHA-256 边累积字节（避免二次读流）。

        大文件可扩展为内容落临时文件后分块回流上传（memmap 方案），
        当前图片评估范围内直接内存累积，见 store_stream 注释。
        """
        sha = hashlib.sha256()
        buffer = bytearray()
        while True:
            chunk = stream.read(CHUNK_SIZE)
            if not chunk:
                break
            sha.update(chunk)
            buffer.extend(chunk)
        return sha.hexdigest(), bytes(buffer)

    async def _upload_bytes(self, object_name: str, content: bytes, content_type: str) -> None:
        self._client.put_object(
            self._bucket,
            object_name,
            io.BytesIO(content),
            length=len(content),
            content_type=content_type,
        )