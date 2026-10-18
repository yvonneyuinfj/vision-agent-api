"""工具调用 spike（立项硬前提）：验证网关模型是否支持 function calling。

ReAct 架构要求驱动循环的模型（LLM_MODEL 与 VLM_MODEL）支持：
1. 纯文本工具调用（模型返回结构化 tool_calls，而非文本协议）；
2. 多模态工具调用（带图消息 + 工具共存）——ReAct 循环"看图后决定调工具"的真实形态；
3. 完整往返：tool_calls -> ToolMessage 回填 -> 模型给出最终回答。

任一模型不达标即输出 FAIL，需先解决网关/模型选型再动手实现。
运行：.venv/bin/python scripts/probe_tool_calling.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from langchain_core.messages import HumanMessage, ToolMessage  # noqa: E402
from langchain_core.tools import tool  # noqa: E402

from app.agents.llm.provider import create_chat_model  # noqa: E402
from app.agents.llm.service import _build_image_blocks, _uses_anthropic_content  # noqa: E402
from app.core.config import get_settings  # noqa: E402

IMAGE = ROOT / "data" / "inputs" / "_upload" / "classify_jieli_cleavage.jpg"


@tool
def invoke_defect_detection(scene: str) -> str:
    """调用指定检测场景的缺陷检测能力，返回检测结果摘要。

    Args:
        scene: 检测场景编码（borescope/infrared/dr/fpi/fracture/metallography）
    """
    return f"[mock] {scene} 检测完成：检出 1 处缺陷，严重度 medium，置信度 0.87"


TOOLS = [invoke_defect_detection]
TASK = (
    "你是质检智能体。请对输入内容执行缺陷检测：判断其检测场景，"
    "然后必须调用 invoke_defect_detection 工具完成检测，最后用一句话汇报结果。"
)


def build_models() -> dict[str, object]:
    s = get_settings()
    is_anthropic = s.llm_provider == "anthropic"
    key = s.anthropic_api_key if is_anthropic else s.dashscope_api_key
    base_url = (
        s.anthropic_base_url
        if is_anthropic
        else (s.llm_base_url or "https://dashscope.aliyuncs.com/api/v1")
    )
    models = {}
    if s.llm_model:
        models[f"LLM_MODEL({s.llm_model})"] = create_chat_model(
            s.llm_provider, base_url=base_url, api_key=key, model=s.llm_model,
            max_retries=s.llm_max_retries,
        )
    if s.vlm_model:
        models[f"VLM_MODEL({s.vlm_model})"] = create_chat_model(
            s.llm_provider, base_url=base_url, api_key=key, model=s.vlm_model,
            max_tokens=s.vlm_max_tokens, timeout_s=s.vlm_timeout_s,
            max_retries=s.llm_max_retries,
        )
    return models


async def check_model(name: str, model) -> dict[str, bool]:
    results = {"text_tool": False, "multimodal_tool": False, "roundtrip": False}
    bound = model.bind_tools(TOOLS)

    # --- 1. 纯文本工具调用 ---
    try:
        resp = await bound.ainvoke([HumanMessage(content=TASK + "\n（输入文件名：classify_jieli_cleavage.jpg）")])
        calls = getattr(resp, "tool_calls", None) or []
        results["text_tool"] = bool(calls)
        print(f"  [{name}] 纯文本: {'✓' if calls else '✗'} "
              f"{[c.get('name') for c in calls] if calls else repr(resp.content)[:120]}")

        # --- 3. 完整往返（依赖第 1 步成功）---
        if calls:
            tc = calls[0]
            tool_msg = ToolMessage(
                content=invoke_defect_detection.invoke(tc["args"]),
                tool_call_id=tc["id"] or "call_spike",
                name=tc.get("name") or "invoke_defect_detection",
            )
            final = await bound.ainvoke([HumanMessage(content=TASK), resp, tool_msg])
            final_text = final.content if isinstance(final.content, str) else str(final.content)
            results["roundtrip"] = bool(final_text.strip()) and not (getattr(final, "tool_calls", None) or [])
            print(f"  [{name}] 往返: {'✓' if results['roundtrip'] else '✗'} 最终回答={final_text[:80]!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [{name}] 纯文本: ✗ 异常 {type(exc).__name__}: {str(exc)[:150]}")

    # --- 2. 多模态工具调用 ---
    try:
        blocks = _build_image_blocks(
            anthropic=_uses_anthropic_content(model),
            prompt=TASK,
            images=[("image/jpeg", IMAGE.read_bytes())],
        )
        resp = await bound.ainvoke([HumanMessage(content=blocks)])
        calls = getattr(resp, "tool_calls", None) or []
        results["multimodal_tool"] = bool(calls)
        args = calls[0].get("args") if calls else None
        print(f"  [{name}] 多模态: {'✓' if calls else '✗'} "
              f"{[c.get('name') for c in calls] if calls else repr(resp.content)[:120]} args={args}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [{name}] 多模态: ✗ 异常 {type(exc).__name__}: {str(exc)[:150]}")

    return results


async def main() -> int:
    models = build_models()
    if not models:
        print("[spike] 未配置 LLM_MODEL/VLM_MODEL，无法验证")
        return 1
    all_ok = True
    for name, model in models.items():
        print(f"\n=== {name} ===")
        r = await check_model(name, model)
        ok = r["text_tool"] and r["multimodal_tool"] and r["roundtrip"]
        all_ok &= ok
        print(f"  [{name}] {'PASS' if ok else 'FAIL'} "
              f"(text={r['text_tool']}, multimodal={r['multimodal_tool']}, roundtrip={r['roundtrip']})")
    print(f"\n[spike] 结论: {'全部通过，ReAct 架构可落地' if all_ok else '存在不达标模型——先解决再动手'}")
    return 0 if all_ok else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
