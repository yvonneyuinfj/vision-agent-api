"""本地软件桥接适配器（需求文档 §5.2：X 射线本地软件"文件投放 + 结果回收"）。

本期定位：占位骨架 + 可运行的演示实现。
- 投放：输入文件拷贝到 in/ 目录并写 manifest.json
- 回收：轮询 out/ 目录下 {task_id}.json，成功后删除投放文件
- 规范 §5.2 要求标准接口改造由软件提供方另行确认，接入前需完成 M1 技术预研

异步纪律：图片拷贝/manifest 写入/结果读取/目录清理均为阻塞文件 IO，全部
经 asyncio.to_thread 下放线程池执行（MB 级图片同步拷贝会卡事件循环，
拖垮并发中的其他任务）；轮询计时用 time.monotonic（单调钟，不受系统
调时影响；asyncio.get_event_loop().time 已废弃且在无循环线程会报错）。
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any

from app.models.capability import CapabilityManifest
from app.core.errors import BizCode, BusinessError
from app.capabilities.adapters.base import AdapterContext, RawToolResponse, ToolAdapter

MANIFEST_NAME = "manifest.json"

_POLL_INTERVAL_S = 1.0


def _stage_inputs(
    task_dir: Path,
    image_paths: list[str],
    auxiliary: dict[str, str],
    *,
    task_id: str,
    trace_id: str,
    params: dict[str, Any],
) -> None:
    """线程池内执行的投放：拷贝输入文件 + 写 manifest（阻塞 IO 集中于此）。"""
    task_dir.mkdir(parents=True, exist_ok=True)
    for path in image_paths:
        shutil.copy2(path, task_dir / Path(path).name)
    for name, path in auxiliary.items():
        shutil.copy2(path, task_dir / name)
    files = [p.name for p in task_dir.iterdir() if p.name != MANIFEST_NAME]
    (task_dir / MANIFEST_NAME).write_text(
        json.dumps(
            {
                "task_id": task_id,
                "trace_id": trace_id,
                "files": files,
                "params": params,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _collect_result(result_path: Path, task_dir: Path) -> dict[str, Any] | None:
    """线程池内执行的回收：读结果文件 + 清理投放目录；未产出返回 None。"""
    if not result_path.exists():
        return None
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # 对方软件可能仍在写入（读到半截 JSON）：本轮跳过，下轮再试
        return None
    result_path.unlink(missing_ok=True)
    shutil.rmtree(task_dir, ignore_errors=True)
    return data if isinstance(data, dict) else None


class LocalSoftwareBridge(ToolAdapter):
    """本地软件桥接：仅快照式投放/回收，不接管软件自身运行生命周期。"""

    def __init__(self, manifest: CapabilityManifest, work_root: str) -> None:
        self._manifest = manifest
        self.tool_name = manifest.tool_name
        self.scene = manifest.scene
        self._work_root = Path(work_root) / manifest.tool_name
        self._in_dir = self._work_root / "in"
        self._out_dir = self._work_root / "out"
        self._in_dir.mkdir(parents=True, exist_ok=True)
        self._out_dir.mkdir(parents=True, exist_ok=True)

    async def health(self) -> tuple[bool, str]:
        # 桥接侧无法探活，返回目录可写性即可（目录探测也是轻量 IO，一并下放）
        def _check() -> bool:
            return self._in_dir.is_dir() and self._out_dir.is_dir()

        ok = await asyncio.to_thread(_check)
        return ok, "bridge dirs ready" if ok else "bridge dirs missing"

    async def invoke(
        self,
        ctx: AdapterContext,
        image_paths: list[str],
        params: dict[str, Any],
        *,
        auxiliary: dict[str, str] | None = None,
    ) -> RawToolResponse:
        # TODO(M1 预研): 与软件方确认输入规格、结果格式与运行机制后完善
        task_dir = self._in_dir / ctx.task_id
        result_path = self._out_dir / f"{ctx.task_id}.json"

        await asyncio.to_thread(
            _stage_inputs,
            task_dir,
            image_paths,
            auxiliary or {},
            task_id=ctx.task_id,
            trace_id=ctx.trace_id,
            params=params,
        )

        deadline = time.monotonic() + ctx.timeout_s
        while time.monotonic() < deadline:
            data = await asyncio.to_thread(_collect_result, result_path, task_dir)
            if data is not None:
                return RawToolResponse(
                    ok=True,
                    code=0,
                    message=str(data.get("message", "success")),
                    payload=data,
                )
            await asyncio.sleep(_POLL_INTERVAL_S)

        await asyncio.to_thread(shutil.rmtree, task_dir, True)
        raise BusinessError(
            BizCode.INFERENCE_TIMEOUT,
            f"local software {self.tool_name} not replied in time",
            task_id=ctx.task_id,
        )
