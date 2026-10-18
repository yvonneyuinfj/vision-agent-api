"""LLMService 门面：持有 langchain BaseChatModel，挂载 YAML 系统提示词。

chat() 挂载 system 消息（可经 system_prompt 参数按次覆盖，如检索后渲染的
default_kb 提示词）；extract_scene() 为纯分类任务仅 user 消息，
优先 vlm 模型回落主模型，失败降级返回 []（保持原 httpx 版语义）。
"""
from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate

from app.agents.llm.prompts import (
    DEFAULT_LANGUAGE,
    EMPTY_KB_CONTEXTS,
    ERROR_SUMMARY_TEMPLATE_ID,
    METRIC_EXPLANATION_TEMPLATE_ID,
    RETRIEVAL_QUERY_TEMPLATE_ID,
    RETRIEVAL_SUFFICIENCY_TEMPLATE_ID,
    SESSION_ROUTING_TEMPLATE_ID,
    TOOL_SELECTION_TEMPLATE_ID,
    format_knowledge_contexts,
    load_system_prompt,
)
from app.core.errors import BizCode, BusinessError
from app.core.logger import get_logger

logger = get_logger("app.agents.llm.service")

# 空回复重试上限：思维链模型可能偶发只吐 thinking 块（text 为空），重试一次换取正常文本
_EXTRACT_SCENE_ATTEMPTS = 2
_EXTRACT_SCENE_RETRY_DELAY_S = 3.0  # 异常重试间隔：防止立即复打仍在缓慢生成的请求


def _text_of(content: Any) -> str:
    """统一提取模型回复文本。

    OpenAI 协议 content 为 str；Anthropic Messages 协议 content 为块列表。
    只拼接 text 块；thinking / tool_use 等非文本块一律跳过——绝不回退
    str()，否则 tool_use 块会以 {'id': 'toolu_...'} 原文污染思考文本。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content) if content is not None else ""


def _parse_json_field(text: str, default: dict[str, Any]) -> dict[str, Any]:
    """LLM 输出 -> JSON dict：剥离 ```json 围栏与前后缀噪声；失败返回 default。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        start, end = cleaned.index("{"), cleaned.rindex("}")
        value = json.loads(cleaned[start:end + 1])
        return value if isinstance(value, dict) else default
    except (ValueError, json.JSONDecodeError):
        return default


def _uses_anthropic_content(model: Any) -> bool:
    """模型是否吃 Anthropic Messages 内容块（而非 OpenAI image_url 块）。"""
    from langchain_anthropic import ChatAnthropic

    return isinstance(model, ChatAnthropic)


def _build_image_blocks(
    *, anthropic: bool, prompt: str, images: list[tuple[str, bytes]]
) -> list[dict[str, Any]]:
    """text + image 混合消息内容块。

    - Anthropic Messages：image 块带 base64 source（media_type + data）；
    - OpenAI 兼容：image_url 块带 data URL。
    images 元素为 (media_type, bytes)，media_type 形如 image/jpeg。
    """
    blocks: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    if anthropic:
        for media_type, data in images:
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": base64.b64encode(data).decode("ascii"),
                    },
                }
            )
        return blocks
    for media_type, data in images:
        b64 = base64.b64encode(data).decode("ascii")
        blocks.append(
            {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}}
        )
    return blocks


_IMAGE_BLOCK_TYPES = {"image", "image_url"}


def _msg_image_count(message: Any) -> int:
    """消息 content 中图片块数量（Anthropic image / OpenAI image_url 两种形态）。"""
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return 0
    return sum(
        1 for b in content if isinstance(b, dict) and b.get("type") in _IMAGE_BLOCK_TYPES
    )


def placeholder_image_blocks(messages: list[Any], *, keep_last_k: int = 1) -> list[Any]:
    """请求时图片折叠（ch02 渐进式披露）：历史消息图片块替换为占位文本。

    - 仅保留最近 keep_last_k 条含图消息的图片块，更早消息中的图片块
      替换为一条 text 块（说明折叠数量），文本块原样保留；
    - 纯构造视图，不修改传入消息对象（持久化的 messages 通道保持原始
      图片，检查点完整性不破坏）；调用方只把返回列表发给模型；
    - 无需折叠（含图消息 <= keep_last_k）时原列表直接返回（零开销）。
    """
    image_indices = [i for i, m in enumerate(messages) if _msg_image_count(m) > 0]
    if len(image_indices) <= keep_last_k:
        return messages

    keep = set(image_indices[len(image_indices) - keep_last_k :]) if keep_last_k > 0 else set()
    folded = set(image_indices) - keep

    result: list[Any] = []
    for i, message in enumerate(messages):
        if i not in folded:
            result.append(message)
            continue
        blocks = list(message.content)
        new_blocks: list[dict[str, Any]] = []
        folded_count = 0
        for block in blocks:
            if isinstance(block, dict) and block.get("type") in _IMAGE_BLOCK_TYPES:
                folded_count += 1
                continue
            new_blocks.append(block)
        if folded_count:
            new_blocks.insert(
                0,
                {
                    "type": "text",
                    "text": (
                        f"[图片已折叠：本条消息原有 {folded_count} 张图，"
                        "为控制上下文已折叠；当前轮次图片保留在最近消息中，"
                        "历史图片分析结论见上文]"
                    ),
                },
            )
        copied = message.model_copy()
        copied.content = new_blocks
        result.append(copied)
    return result


# 证据条目上限：HITL 场景确认载荷瘦身（对齐 L3 原则，避免 interrupt 膨胀）
_SCENE_EVIDENCE_CAP = 3
_SCENE_REASONING_CAP = 200


def _normalize_scene_code(raw: Any) -> str | None:
    """英文场景码归一：仅去空白与大小写折叠（提示词已约定直出场景码）。"""
    if not raw or not isinstance(raw, str):
        return None
    key = raw.strip().lower()
    return key or None


def _parse_scene_routing(raw: str, pool: list[str]) -> list[dict[str, Any]]:
    """解析 tool_selection 新版输出（严格 JSON 路由契约）为富候选列表。

    输出契约（见 config/prompt_template/system_prompt.yaml tool_selection）：
        {"category": 英文场景码, "confidence": 0.0-1.0, "evidence": [...],
         "alternative": 置信度<0.8 时的次可能场景码否则 null, "reasoning": "..."}

    宽容策略：剥离 JSON 围栏；场景码大小写折叠与拼写变体归一；
    仅保留候选池内场景（保序去重）；非 JSON 时回退整段文本按场景码匹配；
    无法识别 -> []（上层走人工确认兜底）。
    """
    data = _parse_json_field(raw, {})
    if not data:
        # 回退：模型未按要求输出 JSON（如直接回答场景码文本）
        scene = _normalize_scene_code(raw.strip())
        if scene is None or scene not in pool:
            return []
        return [{"scene": scene, "source": "llm"}]

    ordered: list[dict[str, Any]] = []

    def _add(category: Any) -> None:
        scene = _normalize_scene_code(category)
        if scene is None or scene not in pool:
            return
        if any(c["scene"] == scene for c in ordered):
            return
        ordered.append({"scene": scene, "source": "llm"})

    _add(data.get("category"))
    if not ordered:
        return []

    primary = ordered[0]
    confidence = data.get("confidence")
    try:
        primary["confidence"] = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        pass
    evidence = data.get("evidence")
    if isinstance(evidence, list):
        kept = [str(e).strip() for e in evidence if str(e).strip()]
        if kept:
            primary["evidence"] = kept[:_SCENE_EVIDENCE_CAP]
    reasoning = data.get("reasoning")
    if isinstance(reasoning, str) and reasoning.strip():
        primary["reasoning"] = reasoning.strip()[:_SCENE_REASONING_CAP]

    alternative = data.get("alternative")
    if alternative not in (None, "", "null"):
        _add(alternative)
        if len(ordered) > 1:
            ordered[1]["role"] = "alternative"
    return ordered


class LLMService:
    def __init__(
        self,
        chat_model: BaseChatModel | None,
        vlm_model: BaseChatModel | None,
        system_prompt: str,
    ) -> None:
        self._chat_model = chat_model
        self._vlm_model = vlm_model
        self._system_prompt = system_prompt
        message_specs: list[tuple[str, str]] = []
        if system_prompt:
            message_specs.append(("system", system_prompt))
        message_specs.append(("human", "{prompt}"))
        self._prompt = ChatPromptTemplate.from_messages(message_specs)

    @property
    def available(self) -> bool:
        return self._chat_model is not None or self._vlm_model is not None

    @property
    def vlm_available(self) -> bool:
        """是否配置了 VLM（多模态）模型：会话轨/agent 轨"看图"能力的前置条件。

        前端据此置灰 agent 轨入口（决策 16：无 VLM 时智能体无法读图）。
        """
        return self._vlm_model is not None

    @property
    def react_model(self) -> BaseChatModel | None:
        """驱动 ReAct 循环的模型：VLM 优先（循环必须看图），无 VLM 降级 chat 模型。

        无 VLM 配置时 agent 轨的场景识别/缺陷判读能力退化，调用方应据此置灰
        agent 轨（决策 16）。
        """
        return self._vlm_model or self._chat_model

    async def chat(self, prompt: str, *, system_prompt: str | None = None) -> str:
        """文本补全：system（YAML 模板）+ human。失败抛 LLM_UNAVAILABLE。

        system_prompt 非 None 时按次覆盖初始化挂载的静态 system
        （空串 = 不挂 system）；None 时沿用初始化提示词。
        """
        if self._chat_model is None:
            raise BusinessError(BizCode.LLM_UNAVAILABLE, "llm has no chat model configured")
        if system_prompt is None:
            messages = self._prompt.format_messages(prompt=prompt)
        else:
            messages = []
            if system_prompt:
                messages.append(SystemMessage(content=system_prompt))
            messages.append(HumanMessage(content=prompt))
        try:
            result = await self._chat_model.ainvoke(messages)
        except Exception as exc:  # noqa: BLE001 - 统一转业务异常
            raise BusinessError(BizCode.LLM_UNAVAILABLE, f"llm error: {exc}") from exc
        return _text_of(result.content)

    async def extract_scene(
        self,
        text: str,
        scenes: list[str],
        *,
        images: list[tuple[str, bytes]] | None = None,
    ) -> list[dict[str, Any]]:
        """场景辅助识别（VLM 图像路由）：返回富候选列表，失败降级返回 []。

        新版 tool_selection 提示词为纯图像路由：输出严格 JSON
        （category 英文场景码 / confidence / evidence / alternative / reasoning），
        由 _parse_scene_routing 归一为平台场景码候选（主候选 + 低置信备选）。
        传入 images（(media_type, bytes) 列表）时以混合内容块多模态发送；
        成功但零命中会打 warning（症结：此前静默，后台看似“无报错”）。
        """
        model = self._vlm_model or self._chat_model
        if model is None:
            raise BusinessError(BizCode.LLM_UNAVAILABLE, "llm has no model configured")
        try:
            # 新版模板为固定路由提示词，无占位符，直接取模板内容
            # （模板 YAML 读取为同步文件 IO：移入工作线程，不占事件循环）
            user_prompt = await asyncio.to_thread(
                load_system_prompt, template_id=TOOL_SELECTION_TEMPLATE_ID
            )
            if images:
                content: Any = _build_image_blocks(
                    anthropic=_uses_anthropic_content(model),
                    prompt=user_prompt,
                    images=images,
                )
            else:
                content = user_prompt
                logger.warning("extract_scene called without images; routing reliability degraded")
        except Exception:  # noqa: BLE001 - 模板渲染失败降级为空候选
            logger.warning("extract_scene prompt build failed; degrade to empty candidates")
            return []
        raw = ""
        for attempt in range(_EXTRACT_SCENE_ATTEMPTS):
            try:
                result = await model.ainvoke([HumanMessage(content=content)])
            except Exception:  # noqa: BLE001 - LLM 失败降级为空候选
                logger.warning(
                    "extract_scene llm call failed on attempt %d/%d",
                    attempt + 1, _EXTRACT_SCENE_ATTEMPTS,
                )
                if attempt + 1 < _EXTRACT_SCENE_ATTEMPTS:
                    # 网关偶发抖动（超时等）瞬时性强，有界重试一次
                    await asyncio.sleep(_EXTRACT_SCENE_RETRY_DELAY_S)
                    continue
                return []
            raw = _text_of(result.content)
            if raw.strip():
                break
            # 200 但无文本（仅 thinking 块等）：可能输出预算被 thinking 吃满，有界重试
            logger.warning(
                "extract_scene got empty text reply on attempt %d/%d; retrying",
                attempt + 1, _EXTRACT_SCENE_ATTEMPTS,
            )
        candidates = _parse_scene_routing(raw, scenes)
        if not candidates:
            logger.warning(
                "extract_scene llm call succeeded but no scene matched: raw=%r pool=%s input=%r",
                raw[:200],
                scenes,
                text,
            )
        return candidates

    async def route_session_image(
        self, images: list[tuple[str, bytes]], scenes: list[str]
    ) -> dict[str, Any]:
        """会话轨图像场景路由（session_routing 模板，含 null 分支）。

        与 extract_scene（tool_selection，必须六选一）不同，本方法允许并保留
        category=null：返回 {"category": 场景码|None, "confidence", "evidence",
        "alternative", "reasoning"}。alternative 为低置信时的次可能场景码
        （供场景确认卡片展示候选排序，优化轮 D5），无则 None。
        任何失败降级为 {"category": None, "reasoning": 说明}，
        不抛异常——会话轨永远要能回复用户。
        """
        fallback = {
            "category": None,
            "confidence": 0.0,
            "evidence": [],
            "alternative": None,
            "reasoning": "图像场景识别暂时不可用（模型服务异常），请直接指定场景或稍后重试。",
        }
        model = self._vlm_model or self._chat_model
        if model is None or not images:
            return fallback
        try:
            user_prompt = await asyncio.to_thread(
                load_system_prompt, template_id=SESSION_ROUTING_TEMPLATE_ID
            )
            content: Any = _build_image_blocks(
                anthropic=_uses_anthropic_content(model), prompt=user_prompt, images=images
            )
        except Exception:  # noqa: BLE001
            logger.warning("route_session_image prompt build failed")
            return fallback
        raw = ""
        for attempt in range(_EXTRACT_SCENE_ATTEMPTS):
            try:
                result = await model.ainvoke([HumanMessage(content=content)])
            except Exception:  # noqa: BLE001
                logger.warning("route_session_image llm call failed (attempt %d)", attempt + 1)
                if attempt + 1 < _EXTRACT_SCENE_ATTEMPTS:
                    await asyncio.sleep(_EXTRACT_SCENE_RETRY_DELAY_S)
                    continue
                return fallback
            raw = _text_of(result.content)
            if raw.strip():
                break
        data = _parse_json_field(raw, {})
        if not data:
            logger.warning("route_session_image non-JSON reply: raw=%r", raw[:200])
            return {**fallback, "reasoning": f"识别输出无法解析，原始回复：{raw[:120]}"}
        scene = _normalize_scene_code(data.get("category"))
        if scene == "null" or scene not in scenes:
            scene = None
        confidence = data.get("confidence")
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = 0.0
        evidence = data.get("evidence")
        evidence = (
            [str(e).strip() for e in evidence if str(e).strip()][:_SCENE_EVIDENCE_CAP]
            if isinstance(evidence, list)
            else []
        )
        reasoning = str(data.get("reasoning") or "").strip()[:_SCENE_REASONING_CAP]
        # 次可能场景（低置信时供场景确认卡片展示候选排序，优化轮 D5）：
        # 归一化后须落在候选池内且不同于主候选，否则记 None
        alternative = _normalize_scene_code(data.get("alternative"))
        if alternative == "null" or alternative not in scenes or alternative == scene:
            alternative = None
        return {
            "category": scene,
            "confidence": confidence,
            "evidence": evidence,
            "alternative": alternative,
            "reasoning": reasoning,
        }

    async def _call_with_template(self, template_id: str, **variables: str) -> str:
        """渲染 YAML 模板 -> 纯任务单轮补全（无 system 前缀）。

        模型不可用/调用失败抛 BusinessError(LLM_UNAVAILABLE)，
        降级语义由各公开方法在自身捕获后决定。
        """
        if self._chat_model is None:
            raise BusinessError(BizCode.LLM_UNAVAILABLE, "llm has no chat model configured")
        user_prompt = await asyncio.to_thread(
            load_system_prompt, template_id=template_id, **variables
        )
        try:
            result = await self._chat_model.ainvoke([HumanMessage(content=user_prompt)])
        except Exception as exc:  # noqa: BLE001
            raise BusinessError(BizCode.LLM_UNAVAILABLE, f"llm error: {exc}") from exc
        return _text_of(result.content).strip()

    async def build_retrieval_queries(
        self,
        *,
        scene: str | None,
        standardized: list[dict[str, Any]],
        language: str = DEFAULT_LANGUAGE,
        missing: str = "",
    ) -> list[str]:
        """检索 query 改写：单次调用生成 2~3 条变体；失败降级返回 []（调用方回退基准问题）。"""
        items = "; ".join(str(i) for i in (standardized or [])[:20]) or "（无标准化明细）"
        variables = {
            "scene": scene or "未识别",
            "standardized_items": items,
            "language": language or DEFAULT_LANGUAGE,
        }
        if missing:
            variables["missing"] = missing
        try:
            text = await self._call_with_template(RETRIEVAL_QUERY_TEMPLATE_ID, **variables)
        except BusinessError:
            return []
        queries = [line.strip().lstrip("- ").strip() for line in text.splitlines()]
        return [q for q in queries if q][:4]

    async def judge_retrieval(
        self, *, question: str, knowledge: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """检索充分性判定。解析失败/LLM 失败统一 fail-open（sufficient=True）：
        不因判定环节故障进入无效的追加检索循环。"""
        retrieved = format_knowledge_contexts(knowledge) or "（检索未命中任何条目）"
        try:
            text = await self._call_with_template(
                RETRIEVAL_SUFFICIENCY_TEMPLATE_ID, question=question, retrieved=retrieved
            )
        except BusinessError:
            return {"sufficient": True, "missing": ""}
        judged = _parse_json_field(text, {"sufficient": True, "missing": ""})
        sufficient = judged.get("sufficient")
        missing = judged.get("missing")
        if not isinstance(sufficient, bool) or not isinstance(missing, str):
            return {"sufficient": True, "missing": ""}
        return {"sufficient": sufficient, "missing": missing}

    async def explain_metrics(
        self,
        *,
        standardized: list[dict[str, Any]],
        knowledge: list[dict[str, Any]],
        language: str = DEFAULT_LANGUAGE,
    ) -> str:
        """专业指标解释（晶粒度/断裂模式/偏移测量等）。失败降级返回空串（报告省略该节）。"""
        if not standardized:
            return ""
        items_text = json.dumps(standardized, ensure_ascii=False)
        contexts = format_knowledge_contexts(knowledge) or EMPTY_KB_CONTEXTS
        try:
            return await self._call_with_template(
                METRIC_EXPLANATION_TEMPLATE_ID,
                items=items_text,
                contexts=contexts,
                language=language or DEFAULT_LANGUAGE,
            )
        except BusinessError:
            return ""

    async def summarize_error(
        self,
        *,
        error: dict[str, Any],
        task_id: str | None = None,
        scene: str | None = None,
        language: str = DEFAULT_LANGUAGE,
    ) -> str:
        """失败任务的可读异常原因总结。失败降级返回空串（报告回退原始 message）。"""
        try:
            return await self._call_with_template(
                ERROR_SUMMARY_TEMPLATE_ID,
                task_id=task_id or "-",
                scene=scene or "未识别",
                error_dump=json.dumps(error, ensure_ascii=False, default=str),
                language=language or DEFAULT_LANGUAGE,
            )
        except BusinessError:
            return ""

    async def aclose(self) -> None:
        """关闭底层客户端（langchain 未暴露关闭方法时 no-op）。"""
        for model in (self._chat_model, self._vlm_model):
            if model is None:
                continue
            close = getattr(model, "aclose", None)
            if callable(close):
                try:
                    await close()
                except Exception:  # noqa: BLE001
                    logger.debug("llm model aclose failed", exc_info=True)
