"""service：system 消息挂载、vlm 路由、异常转 BusinessError、available 判定。

后半部分覆盖 LLMService 新增方法（query 改写 / 充分性判定 / 指标解释 /
异常总结）的降级契约：LLM 失败时返回降级默认值，绝不抛出。
"""
from __future__ import annotations

import base64
import json

import pytest
from langchain_core.messages import AIMessage

from app.agents.llm.prompts import DEFAULT_LANGUAGE
from app.agents.llm.service import (
    LLMService,
    _build_image_blocks,
    _parse_json_field,
    _parse_scene_routing,
    _uses_anthropic_content,
)
from app.core.errors import BizCode, BusinessError


class _RecordingModel:
    """假 BaseChatModel：记录收到的 messages，返回固定回复（或抛异常）。"""

    def __init__(self, reply: str | list = "ok", raise_exc: Exception | None = None) -> None:
        self.calls: list[list] = []
        self._reply = reply
        self._raise_exc = raise_exc

    async def ainvoke(self, messages, **kwargs):
        self.calls.append(messages)
        if self._raise_exc is not None:
            raise self._raise_exc
        return AIMessage(content=self._reply)


class _SequenceModel:
    """按顺序返回多个回复（用完固定在最后一个），用于模拟“先空后正常”的模型抖动。"""

    def __init__(self, replies: list) -> None:
        self.calls: list[list] = []
        self._replies = list(replies)

    async def ainvoke(self, messages, **kwargs):
        self.calls.append(messages)
        if len(self._replies) > 1:
            reply = self._replies.pop(0)
        else:
            reply = self._replies[0]
        return AIMessage(content=reply)


class _FlakyThenOKModel:
    """第一次调用抛异常（模拟超时），其后正常返回固定回复。"""

    def __init__(self, reply: str = "borescope") -> None:
        self.calls: list[list] = []
        self._reply = reply

    async def ainvoke(self, messages, **kwargs):
        self.calls.append(messages)
        if len(self.calls) == 1:
            raise RuntimeError("read timed out")
        return AIMessage(content=self._reply)


def _scene_json(category: str, confidence: float = 0.9, alternative=None, evidence=None) -> str:
    """构造新版 tool_selection 严格 JSON 路由回复。"""
    return json.dumps(
        {
            "category": category,
            "confidence": confidence,
            "evidence": evidence if evidence is not None else ["关键视觉证据"],
            "alternative": alternative,
            "reasoning": "示例决策链",
        },
        ensure_ascii=False,
    )


ANTHROPIC_BLOCKS = [
    {"signature": "", "thinking": "…内部推理…", "type": "thinking"},
    {"text": "fracture", "type": "text"},
]


async def test_chat_mounts_system_prompt():
    model = _RecordingModel(reply="answer")
    service = LLMService(model, None, "SYSTEM-ROLE")

    result = await service.chat("hello")

    assert result == "answer"
    messages = model.calls[0]
    assert messages[0].content == "SYSTEM-ROLE"
    assert messages[1].content == "hello"


async def test_chat_without_system_prompt():
    model = _RecordingModel()
    service = LLMService(model, None, "")

    await service.chat("hi")

    messages = model.calls[0]
    assert len(messages) == 1
    assert messages[0].content == "hi"


async def test_chat_with_system_prompt_override():
    """检索后渲染的 default_kb 提示词可覆盖初始化时的静态 system。"""
    model = _RecordingModel(reply="answer")
    service = LLMService(model, None, "INIT-SYSTEM")

    result = await service.chat("hello", system_prompt="KB-SYSTEM-WITH-CONTEXTS")

    assert result == "answer"
    messages = model.calls[0]
    assert messages[0].content == "KB-SYSTEM-WITH-CONTEXTS"
    assert messages[1].content == "hello"


async def test_chat_with_empty_override_mounts_no_system():
    model = _RecordingModel()
    service = LLMService(model, None, "INIT-SYSTEM")

    await service.chat("hi", system_prompt="")

    messages = model.calls[0]
    assert len(messages) == 1
    assert messages[0].content == "hi"


async def test_chat_wraps_exception_as_business_error():
    model = _RecordingModel(raise_exc=RuntimeError("boom"))
    service = LLMService(model, None, "S")

    with pytest.raises(BusinessError) as exc_info:
        await service.chat("hi")

    assert exc_info.value.code == BizCode.LLM_UNAVAILABLE


async def test_chat_raises_when_no_chat_model():
    service = LLMService(None, _RecordingModel(), "S")

    with pytest.raises(BusinessError) as exc_info:
        await service.chat("hi")

    assert exc_info.value.code == BizCode.LLM_UNAVAILABLE


async def test_extract_scene_routes_to_vlm_without_system_prompt():
    vlm = _RecordingModel(reply=_scene_json("fpi", alternative="metallography"))
    chat = _RecordingModel(reply="unused")
    service = LLMService(chat, vlm, "SYSTEM-ROLE")

    candidates = await service.extract_scene("some input", ["fpi", "metallography", "dr"])

    assert [c["scene"] for c in candidates] == ["fpi", "metallography"]
    assert candidates[0]["source"] == "llm"
    assert candidates[1]["role"] == "alternative"
    assert len(vlm.calls) == 1
    assert len(chat.calls) == 0
    # 纯分类任务仅 user 消息，不挂 system 模板
    assert len(vlm.calls[0]) == 1


async def test_extract_scene_falls_back_to_chat_model():
    chat = _RecordingModel(reply=_scene_json("dr"))
    service = LLMService(chat, None, "S")

    candidates = await service.extract_scene("x", ["dr", "fpi"])

    assert [c["scene"] for c in candidates] == ["dr"]
    assert len(chat.calls) == 1


async def test_extract_scene_prompt_rendered_from_tool_selection_template():
    """场景识别提示词外置于 tool_selection 模板：NDT 路由提示词注入 user 消息。"""
    vlm = _RecordingModel(reply=_scene_json("borescope"))
    service = LLMService(_RecordingModel(), vlm, "S")

    candidates = await service.extract_scene("borescope_01.jpg", ["borescope", "dr"])

    assert [c["scene"] for c in candidates] == ["borescope"]
    prompt_text = vlm.calls[0][0].content
    # 新模板为固定 NDT 路由提示词（无 candidates/input 占位符）
    assert "无损检测" in prompt_text
    assert "孔探" in prompt_text
    assert "{{" not in prompt_text


async def test_extract_scene_filters_unknown_and_returns_empty_on_error():
    # 主候选在池内、备选为未知类目 -> 备选被过滤
    noisy = _RecordingModel(reply=_scene_json("dr", alternative="unknown_scene"))
    service = LLMService(noisy, None, "S")
    assert [c["scene"] for c in await service.extract_scene("x", ["dr"])] == ["dr"]

    broken = _RecordingModel(raise_exc=RuntimeError("down"))
    service = LLMService(_RecordingModel(), broken, "S")
    assert await service.extract_scene("x", ["dr"]) == []


async def test_chat_extracts_anthropic_content_blocks():
    """Anthropic Messages 协议 content 为块列表：只拼接 text、跳过 thinking。"""
    model = _RecordingModel(reply=ANTHROPIC_BLOCKS)
    service = LLMService(model, None, "S")

    result = await service.chat("hi")

    assert result == "fracture"


async def test_chat_handles_multiple_text_blocks_and_none_content():
    model = _RecordingModel(reply=[{"text": "a", "type": "text"}, {"text": "b", "type": "text"}])
    service = LLMService(model, None, "S")
    assert await service.chat("hi") == "ab"

    empty = _RecordingModel(reply=[{"thinking": "x", "type": "thinking"}])
    service = LLMService(empty, None, "S")
    assert await service.chat("hi") == ""


async def test_extract_scene_parses_anthropic_blocks_with_json_text():
    """Anthropic 块列表：跳过 thinking 块，解析 text 块里的路由 JSON。"""
    vlm = _RecordingModel(
        reply=[
            {"thinking": "…", "type": "thinking"},
            {"text": _scene_json("fracture"), "type": "text"},
        ]
    )
    service = LLMService(_RecordingModel(), vlm, "S")

    candidates = await service.extract_scene(
        "x", ["borescope", "metallography", "fpi", "dr", "fracture"]
    )

    assert [c["scene"] for c in candidates] == ["fracture"]


# ---------------------------------------------------------------------------
# 多模态场景识别：图片字节以消息内容块形式发送
# ---------------------------------------------------------------------------


async def test_extract_scene_attaches_image_blocks_for_openai_compatible_model():
    """非 Anthropic 模型：text 块 + image_url data URL。"""
    vlm = _RecordingModel(reply=_scene_json("fracture"))
    service = LLMService(_RecordingModel(), vlm, "S")
    payload = b"\xff\xd8fakejpeg"

    candidates = await service.extract_scene(
        "class3_1.jpg", ["borescope", "fracture"], images=[("image/jpeg", payload)]
    )

    assert [c["scene"] for c in candidates] == ["fracture"]
    content = vlm.calls[0][0].content
    assert isinstance(content, list)
    text_block, image_block = content
    assert "无损检测" in text_block["text"]
    assert image_block["type"] == "image_url"
    assert image_block["image_url"]["url"] == "data:image/jpeg;base64," + base64.b64encode(payload).decode()


async def test_extract_scene_keeps_plain_text_message_without_images():
    """不传图片时维持纯文本消息形态（旧 provider 兼容）。"""
    vlm = _RecordingModel(reply=_scene_json("borescope"))
    service = LLMService(_RecordingModel(), vlm, "S")

    await service.extract_scene("class3_1.jpg", ["borescope"])

    assert isinstance(vlm.calls[0][0].content, str)
    assert "无损检测" in vlm.calls[0][0].content


async def test_extract_scene_logs_warning_when_llm_matches_no_scene(caplog, monkeypatch):
    """成功但零命中必须留痕（此前静默失败导致“后台无报错”）。"""
    import logging

    from app.agents.llm import service as llm_service_module

    plain_logger = logging.getLogger("test.extract_scene")
    monkeypatch.setattr(llm_service_module, "logger", plain_logger)
    vlm = _RecordingModel(reply="无法判断")
    service = LLMService(_RecordingModel(), vlm, "S")

    with caplog.at_level(logging.WARNING):
        scenes = await service.extract_scene("class3_1.jpg", ["borescope", "fracture"])

    assert scenes == []
    assert any("no scene matched" in record.message for record in caplog.records)


async def test_extract_scene_retries_once_when_reply_is_empty():
    """模型 200 但只吐 thinking 块（文本为空）：有界重试一次取到正常回复。"""
    vlm = _SequenceModel(
        [
            [{"thinking": "…", "type": "thinking"}],
            [{"thinking": "…", "type": "thinking"}, {"text": _scene_json("fracture"), "type": "text"}],
        ]
    )
    service = LLMService(_RecordingModel(), vlm, "S")

    candidates = await service.extract_scene("class3_1.jpg", ["borescope", "fracture"])

    assert [c["scene"] for c in candidates] == ["fracture"]
    assert len(vlm.calls) == 2


async def test_extract_scene_does_not_retry_when_reply_has_text_but_no_hit():
    """回复非空但不命中候选：不是抖动，不浪费第二次调用。"""
    vlm = _SequenceModel(["（空）", "fracture"])
    service = LLMService(_RecordingModel(), vlm, "S")

    scenes = await service.extract_scene("class3_1.jpg", ["borescope", "fracture"])

    assert scenes == []
    assert len(vlm.calls) == 1


async def test_extract_scene_two_empty_replies_degrades_to_empty():
    """连续两次空回复：收敛为空候选（零命中 warning 仍会留痕）。"""
    vlm = _SequenceModel([[{"thinking": "…", "type": "thinking"}], []])
    service = LLMService(_RecordingModel(), vlm, "S")

    scenes = await service.extract_scene("class3_1.jpg", ["borescope", "fracture"])

    assert scenes == []
    assert len(vlm.calls) == 2


async def test_extract_scene_retries_when_first_call_raises():
    """第一次调用抛异常（如网关抖动超时）：有界重试后取到正常回复。

    回复为纯场景码（非 JSON）：走容错回退路径仍可映射。
    """
    vlm = _FlakyThenOKModel(reply="borescope")
    service = LLMService(_RecordingModel(), vlm, "S")

    candidates = await service.extract_scene("class3_1.jpg", ["borescope", "fracture"])

    assert [c["scene"] for c in candidates] == ["borescope"]
    assert len(vlm.calls) == 2


async def test_extract_scene_degrades_when_all_attempts_raise():
    """每次调用都抛异常：最终降级空候选，绝不抛出。"""
    vlm = _RecordingModel(raise_exc=RuntimeError("down"))
    service = LLMService(_RecordingModel(), vlm, "S")

    scenes = await service.extract_scene("x", ["borescope"])

    assert scenes == []
    assert len(vlm.calls) == 2


def test_build_image_blocks_anthropic_style():
    blocks = _build_image_blocks(
        anthropic=True, prompt="P", images=[("image/jpeg", b"j")]
    )
    assert blocks[0] == {"type": "text", "text": "P"}
    assert blocks[1]["type"] == "image"
    assert blocks[1]["source"] == {
        "type": "base64",
        "media_type": "image/jpeg",
        "data": base64.b64encode(b"j").decode("ascii"),
    }


def test_build_image_blocks_openai_style_data_url():
    blocks = _build_image_blocks(
        anthropic=False, prompt="P", images=[("image/png", b"p")]
    )
    assert blocks[1]["type"] == "image_url"
    assert blocks[1]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(b"p").decode("ascii")


def test_uses_anthropic_content_blocks_by_model_type():
    from app.agents.llm.provider import create_chat_model

    anthropic_model = create_chat_model(
        "anthropic",
        base_url="https://maas-api.antdigital.com",
        api_key="sk-test",
        model="qwen3.8-max",
    )
    openai_model = create_chat_model(
        "openai_compatible",
        base_url="https://example.com/v1",
        api_key="sk-test",
        model="q",
    )
    assert _uses_anthropic_content(anthropic_model) is True
    assert _uses_anthropic_content(openai_model) is False
    assert _uses_anthropic_content(None) is False


def test_available():
    assert LLMService(_RecordingModel(), None, "").available is True
    assert LLMService(None, _RecordingModel(), "").available is True
    assert LLMService(None, None, "").available is False


# ---------------------------------------------------------------------------
# 新增方法：查询改写 / 充分性判定 / 指标解释 / 异常总结（降级契约）
# ---------------------------------------------------------------------------


def _svc(response: str | None) -> LLMService:
    """response 为 None 时模拟 LLM 不可用（走降级路径）。"""
    model = (
        _RecordingModel(raise_exc=RuntimeError("llm down"))
        if response is None
        else _RecordingModel(reply=response)
    )
    return LLMService(model, None, system_prompt="")


async def test_build_retrieval_queries_parses_lines_and_strips_prefix():
    svc = _svc("- 点蚀缺陷判级标准\n- 内窥检测方法规范\n- 历史点蚀案例")
    queries = await svc.build_retrieval_queries(scene="borescope", standardized=[],
                                                language=DEFAULT_LANGUAGE)
    assert len(queries) == 3
    assert queries[0] == "点蚀缺陷判级标准"
    # 渲染后的 user prompt 携带调用上下文（场景注入）
    assert "borescope" in svc._chat_model.calls[0][0].content


async def test_build_retrieval_queries_degrades_to_empty_on_llm_failure():
    svc = _svc(None)
    assert await svc.build_retrieval_queries(scene=None, standardized=[], language="中文") == []


async def test_judge_retrieval_parses_json_object():
    svc = _svc('{"sufficient": false, "missing": "缺少判级标准条款"}')
    judged = await svc.judge_retrieval(question="Q", knowledge=[])
    assert judged == {"sufficient": False, "missing": "缺少判级标准条款"}


async def test_judge_retrieval_fail_open_on_garbage_and_llm_failure():
    for response in ('```json\n{"sufficient":', "完全不是 JSON", None,
                     '{"sufficient": "false", "missing": "缺"}'):
        judged = await _svc(response).judge_retrieval(question="Q", knowledge=[])
        assert judged == {"sufficient": True, "missing": ""}


def test_parse_json_field_strips_code_fences():
    assert _parse_json_field('```json\n{"a": 1}\n```', {}) == {"a": 1}
    assert _parse_json_field("前缀噪声 {\"a\": 1} 后缀", {}) == {"a": 1}
    assert _parse_json_field("不是 json", {}) == {}


# ---------------------------------------------------------------------------
# 新版路由 JSON 契约解析（_parse_scene_routing）
# ---------------------------------------------------------------------------

POOL = ["borescope", "infrared", "dr", "fpi", "fracture", "metallography"]


def test_parse_scene_routing_full_json_contract():
    raw = _scene_json("fpi", confidence=0.93, evidence=["黑背景", "黄绿色迹痕", "强对比", "多余项"])
    candidates = _parse_scene_routing(raw, POOL)

    assert len(candidates) == 1
    primary = candidates[0]
    assert primary["scene"] == "fpi"
    assert primary["source"] == "llm"
    assert primary["confidence"] == 0.93
    assert primary["evidence"] == ["黑背景", "黄绿色迹痕", "强对比"]  # 上限 3 条
    assert primary["reasoning"] == "示例决策链"


def test_parse_scene_routing_alternative_becomes_second_candidate():
    raw = _scene_json("fracture", confidence=0.62, alternative="metallography")
    candidates = _parse_scene_routing(raw, POOL)

    assert [c["scene"] for c in candidates] == ["fracture", "metallography"]
    assert candidates[1]["role"] == "alternative"
    assert "confidence" not in candidates[1]


def test_parse_scene_routing_dedupes_alternative_equal_to_primary():
    raw = _scene_json("fracture", alternative="fracture")
    assert [c["scene"] for c in _parse_scene_routing(raw, POOL)] == ["fracture"]


def test_parse_scene_routing_filters_category_outside_pool():
    raw = _scene_json("borescope")  # 池内无 borescope
    assert _parse_scene_routing(raw, ["dr", "fpi"]) == []


def test_parse_scene_routing_plain_text_fallback_and_garbage():
    # 模型未按要求输出 JSON：整段文本按场景码匹配（容错回退）
    assert [c["scene"] for c in _parse_scene_routing("infrared", POOL)] == ["infrared"]
    # 围栏包裹的 JSON 也可解析
    fenced = "```json\n" + _scene_json("dr") + "\n```"
    assert [c["scene"] for c in _parse_scene_routing(fenced, POOL)] == ["dr"]
    # 无法识别 -> 空（上层走人工确认兜底）
    assert _parse_scene_routing("无法判断", POOL) == []
    assert _parse_scene_routing('{"category": "unknown_scene"}', POOL) == []
    assert _parse_scene_routing("", POOL) == []


def test_parse_scene_routing_invalid_confidence_and_alternative_null():
    raw = json.dumps(
        {"category": "metallography", "confidence": "很高", "alternative": None},
        ensure_ascii=False,
    )
    candidates = _parse_scene_routing(raw, POOL)
    assert len(candidates) == 1
    assert "confidence" not in candidates[0]  # 非法置信度丢弃而非报错


def test_normalize_scene_code_case_and_spelling_variants():
    """提示词直出英文场景码：解析层只做大小写折叠与去空白，不维护别名映射。"""
    from app.agents.llm.service import _normalize_scene_code

    assert _normalize_scene_code("Fracture") == "fracture"
    assert _normalize_scene_code(" FPI ") == "fpi"
    assert _normalize_scene_code("Metallography") == "metallography"
    # 大小写混写的完整 JSON 同样可解析
    assert [c["scene"] for c in _parse_scene_routing(_scene_json("BORESCOPE"), POOL)] == ["borescope"]
    assert _normalize_scene_code("") is None
    assert _normalize_scene_code(None) is None
    assert _normalize_scene_code(123) is None


async def test_explain_metrics_degrades_to_empty_string():
    assert await _svc(None).explain_metrics(standardized=[], knowledge=[], language="中文") == ""


async def test_summarize_error_injects_error_dump_and_degrades():
    svc = _svc("检测服务未装配，能力调用失败。")
    text = await svc.summarize_error(error={"node": "invoke_capability", "code": 4001,
                                            "message": "tool missing"},
                                     task_id="T1", scene="borescope")
    assert text == "检测服务未装配，能力调用失败。"
    assert "execute" not in text
    assert await _svc(None).summarize_error(error={"message": "x"}, language="中文") == ""


# --------------------------------------------------------------------------
# route_session_image（会话轨场景路由）：null 分支 + alternative 次候选（优化轮 D5）
# --------------------------------------------------------------------------
async def test_route_session_image_parses_alternative():
    vlm = _RecordingModel(reply=_scene_json("fracture", confidence=0.45, alternative="metallography"))
    service = LLMService(None, vlm, "S")
    out = await service.route_session_image([("image/jpeg", b"\x89PNG")], ["fracture", "metallography", "dr"])
    assert out["category"] == "fracture"
    assert out["confidence"] == 0.45
    assert out["alternative"] == "metallography"


async def test_route_session_image_alternative_filtered_when_outside_pool():
    vlm = _RecordingModel(reply=_scene_json("fracture", confidence=0.4, alternative="borescope"))
    service = LLMService(None, vlm, "S")
    out = await service.route_session_image([("image/jpeg", b"\x89PNG")], ["fracture", "metallography"])
    assert out["category"] == "fracture"
    assert out["alternative"] is None  # borescope 不在候选池


async def test_route_session_image_alternative_dropped_when_same_as_primary():
    vlm = _RecordingModel(reply=_scene_json("dr", confidence=0.5, alternative="dr"))
    service = LLMService(None, vlm, "S")
    out = await service.route_session_image([("image/jpeg", b"\x89PNG")], ["dr", "fpi"])
    assert out["alternative"] is None


async def test_route_session_image_null_category_kept():
    vlm = _RecordingModel(reply=json.dumps(
        {"category": None, "confidence": 0.0, "evidence": [], "alternative": None, "reasoning": "生活照"},
        ensure_ascii=False))
    service = LLMService(None, vlm, "S")
    out = await service.route_session_image([("image/jpeg", b"\x89PNG")], ["dr", "fpi"])
    assert out["category"] is None
    assert out["alternative"] is None


async def test_route_session_image_fallback_without_model():
    service = LLMService(None, None, "S")
    out = await service.route_session_image([("image/jpeg", b"\x89PNG")], ["dr"])
    assert out["category"] is None
    assert out["alternative"] is None
    assert out["confidence"] == 0.0


# --------------------------------------------------------------------------
# _text_of：只取 text 块，thinking/tool_use 一律跳过（共识轮污染回归）
# --------------------------------------------------------------------------
def test_text_of_returns_plain_string():
    from app.agents.llm.service import _text_of
    assert _text_of("纯文本") == "纯文本"


def test_text_of_concatenates_text_blocks():
    from app.agents.llm.service import _text_of
    content = [{"type": "text", "text": "第一段。"}, {"type": "text", "text": "第二段。"}]
    assert _text_of(content) == "第一段。第二段。"


def test_text_of_skips_thinking_and_tool_use_blocks():
    """tool_use 块绝不能回退 str() 混入文本（曾以 {'id':'toolu_...'} 污染思考）。"""
    from app.agents.llm.service import _text_of
    content = [
        {"type": "thinking", "thinking": "内部推理不外显"},
        {"type": "text", "text": "可见思考。"},
        {"type": "tool_use", "id": "toolu_abc", "name": "invoke_capability",
         "input": {"scene": "fpi"}},
    ]
    assert _text_of(content) == "可见思考。"
    assert "toolu_abc" not in _text_of(content)


def test_text_of_none_is_empty():
    from app.agents.llm.service import _text_of
    assert _text_of(None) == ""
