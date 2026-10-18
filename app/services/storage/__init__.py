"""对象存储服务导出。"""
from app.services.storage.minio_service import DEFAULT_BUCKET, StorageService

__all__ = ["StorageService", "DEFAULT_BUCKET"]