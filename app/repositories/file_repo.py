"""文件仓储：原始输入、报告与 artifact 落盘（data 目录），对外只暴露安全 URI。"""
from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings

# 安全读取白名单：只允许回归 uri 中提供的文件，禁止任意路径（规范 §14）
# 含 .opi（X光/DR 真实工具的 OPI 序列数据文件，非代码，2026-09-22 新增）
_SAFE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".csv", ".json", ".md", ".pdf", ".opi", ".pptx"}


class FileRepo:
    def __init__(self, root: str | None = None) -> None:
        # 构造期一次性绝对化：运行期（事件循环内）不再触发 os.getcwd——
        # LangGraph Studio 的阻塞调用检测（blockbuster）会拦截循环内
        # getcwd/resolve 等同步系统调用
        self._root = Path(root or get_settings().data_path).resolve()
        (self._root / "inputs").mkdir(parents=True, exist_ok=True)
        (self._root / "artifacts").mkdir(parents=True, exist_ok=True)
        (self._root / "reports").mkdir(parents=True, exist_ok=True)

    # ---------- 写入 ----------
    def save_bytes(self, task_id: str, name: str, content: bytes, kind: str = "inputs") -> str:
        """落盘并返回业务 URI（不暴露绝对路径）。"""
        name = Path(name).name  # 防目录穿越
        if Path(name).suffix.lower() not in _SAFE_EXTS:
            raise ValueError(f"unsupported file extension: {name}")
        target_dir = self._root / kind / task_id
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / name).write_bytes(content)
        return self.uri_for(task_id, name, kind)

    def save_text(self, task_id: str, name: str, content: str, kind: str = "reports") -> str:
        return self.save_bytes(task_id, name, content.encode("utf-8"), kind)

    # ---------- 读取 ----------
    def uri_to_path(self, uri: str) -> Path:
        """业务 URI -> 本地路径（仅限 data 根内）。

        URI 形态: /api/v1/tasks/{task_id}/files/{name}?kind={inputs|artifacts|reports}
        畸形 URI（段数不足/形态不符/扩展名不在白名单）抛 ValueError（400 语义），
        绝不以 IndexError 逃逸为 500。

        安全性：task_id/name 逐段校验（禁 ".."/路径分隔符）后与绝对根
        self._root 纯语法拼接——不调用 resolve()，避免运行期 os.getcwd/
        realpath 同步系统调用（事件循环阻塞检测下会报错）。
        """
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(uri)
        parts = parsed.path.strip("/").split("/")  # [api, v1, tasks, {task_id}, files, {name}]
        if len(parts) < 6 or parts[0] != "api" or parts[2] != "tasks" or parts[4] != "files":
            raise ValueError(f"invalid file uri: {uri}")
        task_id = parts[3]
        name = parts[5]
        for seg in (task_id, name):
            if not seg or seg in (".", "..") or "/" in seg or "\\" in seg or "\x00" in seg:
                raise ValueError(f"invalid file uri: {uri}")
        if Path(name).suffix.lower() not in _SAFE_EXTS:
            raise ValueError(f"unsupported file extension: {name}")
        query = parse_qs(parsed.query)
        kind = query.get("kind", ["inputs"])[0]
        if kind not in {"inputs", "artifacts", "reports"}:
            raise ValueError(f"invalid kind: {kind}")
        return self._root / kind / task_id / name

    @staticmethod
    def uri_for(task_id: str, name: str, kind: str) -> str:
        return f"/api/v1/tasks/{task_id}/files/{name}?kind={kind}"