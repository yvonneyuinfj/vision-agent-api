"""一次性探针：用真实 VLM 验证新版 tool_selection 图像路由提示词端到端。

构建方式与 app/services/container.py 完全一致；对若干真实图片调用
extract_scene（全场景池 + 图片），打印富候选（主选 + 置信度 + 备选）。
运行：.venv/bin/python scripts/probe_scene_routing.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agents.llm import LLMService, create_chat_model, load_system_prompt  # noqa: E402
from app.core.config import get_settings  # noqa: E402

UPLOAD = ROOT / "data" / "inputs" / "_upload"
POOL = ["borescope", "infrared", "dr", "fpi", "fracture", "metallography"]

# 探针样本：文件名 -> 期望场景（人工标注，供比对）
SAMPLES = {
    "classify_jieli_cleavage.jpg": "fracture",
    "classify_renwo_dimple.jpg": "fracture",
    "yingguang_yolox_test.jpg": "fpi",
    "hongwai.png": "infrared",
    "kongtan.jpg": "borescope",
}

_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def build_llm() -> LLMService | None:
    s = get_settings()
    is_anthropic = s.llm_provider == "anthropic"
    key = s.anthropic_api_key if is_anthropic else s.dashscope_api_key
    if not key:
        return None
    base_url = (
        s.anthropic_base_url
        if is_anthropic
        else (s.llm_base_url or "https://dashscope.aliyuncs.com/api/v1")
    )
    vlm_model = (
        create_chat_model(
            s.llm_provider,
            base_url=base_url,
            api_key=key,
            model=s.vlm_model,
            max_tokens=s.vlm_max_tokens,
            timeout_s=s.vlm_timeout_s,
            max_retries=s.llm_max_retries,
        )
        if s.vlm_model
        else None
    )
    chat_model = (
        create_chat_model(
            s.llm_provider, base_url=base_url, api_key=key, model=s.llm_model,
            max_retries=s.llm_max_retries,
        )
        if s.llm_model
        else None
    )
    return LLMService(chat_model, vlm_model, load_system_prompt())


async def main() -> int:
    llm = build_llm()
    if llm is None or not llm.available:
        print("[probe] 无可用 LLM/VLM 配置，跳过")
        return 0

    ok = 0
    for name, expected in SAMPLES.items():
        path = UPLOAD / name
        if not path.is_file():
            print(f"[skip] {name} 不存在")
            continue
        images = [(_MIME[path.suffix.lower()], path.read_bytes())]
        try:
            candidates = await llm.extract_scene(name, POOL, images=images)
        except Exception as exc:  # noqa: BLE001
            print(f"[error] {name}: {exc}")
            continue
        if not candidates:
            print(f"[empty] {name}: 无候选（期望 {expected}）")
            continue
        primary = candidates[0]
        hit = primary["scene"] == expected
        ok += int(hit)
        mark = "✓" if hit else "✗"
        alt = next((c["scene"] for c in candidates[1:]), "-")
        print(
            f"[{mark}] {name}: primary={primary['scene']} "
            f"(conf={primary.get('confidence')}, expected={expected}) alt={alt}"
        )
        if primary.get("reasoning"):
            print(f"      reasoning: {primary['reasoning']}")
    print(f"\n[probe] 命中 {ok}/{len(SAMPLES)}")
    await llm.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
