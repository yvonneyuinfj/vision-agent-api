"""图片折叠（请求时视图变换）测试：占位符化、原消息不可变、会话轨接线。"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.llm.service import placeholder_image_blocks


def _img_msg(text: str, n: int = 1) -> HumanMessage:
    blocks: list[dict] = [{"type": "text", "text": text}]
    for i in range(n):
        blocks.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": f"img{i}"},
            }
        )
    return HumanMessage(content=blocks)


def _openai_img_msg(text: str) -> HumanMessage:
    return HumanMessage(
        content=[
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abc"}},
        ]
    )


def test_folds_older_images_keeps_recent():
    """两条含图消息：保留最近一条的图片，较早的替换为占位文本。"""
    m1 = _img_msg("第一张图")
    m2 = _img_msg("第二张图")
    m3 = AIMessage(content="好的，我来分析")

    out = placeholder_image_blocks([m1, m2, m3], keep_last_k=1)

    # m1 折叠：图片块消失，出现占位文本
    assert isinstance(out[0], HumanMessage)
    types = [b["type"] for b in out[0].content]
    assert "image" not in types
    assert any("图片已折叠" in b.get("text", "") for b in out[0].content if b["type"] == "text")
    assert any("本条消息原有 1 张图" in b.get("text", "") for b in out[0].content if b["type"] == "text")
    # m2 保留图片
    assert any(b["type"] == "image" for b in out[1].content)
    # 纯文本消息原样
    assert out[2] is m3


def test_no_fold_when_within_budget():
    """含图消息数 <= keep_last_k：原列表直接返回（零开销）。"""
    m1 = _img_msg("唯一一张图")
    out = placeholder_image_blocks([m1], keep_last_k=1)
    assert out is [m1] or (len(out) == 1 and out[0] is m1)


def test_original_messages_not_mutated():
    """折叠是纯视图：原消息的图片块必须原封不动（检查点完整性）。"""
    m1 = _img_msg("第一张", n=2)
    m2 = _img_msg("第二张")

    out = placeholder_image_blocks([m1, m2], keep_last_k=1)

    assert len([b for b in m1.content if b["type"] == "image"]) == 2
    assert len([b for b in m2.content if b["type"] == "image"]) == 1
    assert out[0] is not m1  # 返回的是拷贝
    # 占位说明含折叠数量
    assert any("2 张图" in b.get("text", "") for b in out[0].content if b["type"] == "text")


def test_openai_image_url_blocks_folded():
    """OpenAI image_url 块同样折叠（两种 provider 形态都处理）。"""
    m1 = _openai_img_msg("旧图")
    m2 = _img_msg("新图")

    out = placeholder_image_blocks([m1, m2], keep_last_k=1)

    types = [b["type"] for b in out[0].content]
    assert "image_url" not in types
    assert "text" in types


def test_string_content_messages_untouched():
    """纯字符串 content 的消息（无图片块）：不进入折叠候选。"""
    m1 = HumanMessage(content="纯文本历史")
    m2 = _img_msg("当前图")

    out = placeholder_image_blocks([m1, m2], keep_last_k=1)

    assert out[0] is m1
    assert any(b["type"] == "image" for b in out[1].content)


def test_keep_last_zero_folds_all():
    m1 = _img_msg("图1")
    m2 = _img_msg("图2")

    out = placeholder_image_blocks([m1, m2], keep_last_k=0)

    assert not any(b["type"] in {"image", "image_url"} for m in out for b in m.content)
    assert any("图片已折叠" in b.get("text", "") for m in out for b in m.content if b["type"] == "text")
