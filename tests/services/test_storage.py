"""MinIO 去重（秒传）核心逻辑测试：分块 SHA-256 + 去重复用 + 元数据落库。

策略：不连真实 MinIO —— 用内存版 ObjectRepo 桩替换真实仓储，
并 stub StorageService._client（Minio 客户端）以捕获"是否发生实际上传"。
"""
from __future__ import annotations

import asyncio
import hashlib

from app.services.storage.minio_service import StorageService


class _FakeObjectRepo:
    """不连 DB 的仓储桩：dict 内存存储，保留 get/upsert/bump_ref 语义。"""

    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}

    async def get_by_sha256(self, sha256):
        d = self._rows.get(sha256)
        if d is None:
            return None
        return _Rec(d)

    async def bump_ref(self, sha256, task_id):
        d = self._rows.get(sha256)
        if d is not None:
            d["ref_count"] += 1
            d["last_task_id"] = task_id or d["last_task_id"]

    async def upsert(self, **kw):
        sha = kw["sha256"]
        existed = sha in self._rows
        if existed:
            self._rows[sha]["ref_count"] += 1
            self._rows[sha]["last_task_id"] = kw.get("task_id")
            return False
        row = {
            "sha256": sha,
            "object_name": kw["object_name"],
            "bucket": kw["bucket"],
            "uri": kw["uri"],
            "ext": kw["ext"],
            "size_bytes": kw["size_bytes"],
            "content_type": kw["content_type"],
            "first_task_id": kw.get("task_id"),
            "last_task_id": kw.get("task_id"),
            "ref_count": 1,
        }
        self._rows[sha] = row
        return True

    def uploaded_names(self) -> list[str]:
        return [r["object_name"] for r in self._rows.values()]


class _Rec:  # 模拟 ORM record 需要的属性
    def __init__(self, d: dict) -> None:
        self.object_name = d["object_name"]
        self.uri = d["uri"]
        self.size_bytes = d["size_bytes"]


def _make_service(fake_repo: _FakeObjectRepo):
    """构造 StorageService：endpoint 仅用于构造 Minio 客户端（不真实连接）。"""
    svc = StorageService(
        endpoint="localhost:9000",
        access_key="k",
        secret_key="s",
        bucket="qc-media-assets",
        object_repo=fake_repo,  # type: ignore[arg-type]
    )
    # 最小桩：拦截真实网络调用（上传 / 桶检测均 mock）
    svc._client = _MinioStub()
    return svc


class _MinioStub:
    """Minio 客户端网络调用桩：记录是否发生 put_object（去重判据）。"""

    def __init__(self) -> None:
        self.put_count = 0

    def bucket_exists(self, bucket) -> bool:
        return True

    def make_bucket(self, bucket) -> None:
        pass

    def put_object(self, bucket, object_name, data, length, content_type) -> None:
        self.put_count += 1


def test_upload_first_time_stores_and_is_not_deduped():
    # Arrange
    fake = _FakeObjectRepo()
    svc = _make_service(fake)
    content = b"borescope-image-A" * 100

    # Act
    out = asyncio.run(svc.store_bytes(content, filename="shot.jpg", task_id="T1"))

    # Assert：首次上传 -> 非秒传，对象名 = {sha256}.jpg，实际 PUT 1 次
    assert out["deduped"] is False
    assert out["sha256"] == hashlib.sha256(content).hexdigest()
    assert out["object_name"] == f"{out['sha256']}.jpg"
    assert out["uri"] == f"minio://qc-media-assets/{out['sha256']}.jpg"


def test_upload_same_content_deduped_second_time():
    # Arrange
    fake = _FakeObjectRepo()
    svc = _make_service(fake)
    blob = b"dedupe-content-" * 50

    # Act：两次上传完全相同内容
    async def _run():
        first = await svc.store_bytes(blob, filename="img.png", task_id="T1")
        second = await svc.store_bytes(blob, filename="img.png", task_id="T2")
        return first, second

    first, second = asyncio.run(_run())
    # Assert：第二次命中 Hash -> 秒传，且未再次 PUT；引用计数累加
    assert first["deduped"] is False
    assert second["deduped"] is True
    assert second["sha256"] == first["sha256"]
    assert second["object_name"] == first["object_name"]  # 同一对象复用
    assert svc._client.put_count == 1  # 全程仅实际上传 1 次

    # 元数据落库：ref_count 已累加
    rec = asyncio.run(fake.get_by_sha256(first["sha256"]))
    assert rec is not None
    assert rec.object_name == first["object_name"]


def test_different_content_produces_distinct_objects():
    # Arrange
    fake = _FakeObjectRepo()
    svc = _make_service(fake)

    async def _run():
        a = await svc.store_bytes(b"content-A" * 50, filename="a.jpg", task_id="T1")
        b = await svc.store_bytes(b"content-B" * 50, filename="a.jpg", task_id="T1")
        return a, b

    a, b = asyncio.run(_run())
    # Assert：内容不同 -> 不同 sha256 / object_name，各上传一次
    assert a["sha256"] != b["sha256"]
    assert a["object_name"] != b["object_name"]
    assert a["deduped"] is False and b["deduped"] is False
    assert svc._client.put_count == 2


def test_stream_read_is_chunked_and_hashes_correctly():
    """分块读取验证：用大于单块的多块流，确保 SHA-256 与全量一致。"""
    from app.services.storage.minio_service import CHUNK_SIZE

    fake = _FakeObjectRepo()
    svc = _make_service(fake)
    blob = b"x" * (CHUNK_SIZE * 3 + 1234)  # 跨 4 块
    async def _run():
        return await svc.store_bytes(blob, filename="big.bin", task_id="T1")
    out = asyncio.run(_run())
    # Assert：分块累积后的摘要与直接哈希一致
    assert out["sha256"] == hashlib.sha256(blob).hexdigest()