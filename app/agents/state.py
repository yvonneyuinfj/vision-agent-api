"""LangGraph AgentState 定义。

遵循需求文档 §2.2 端到端流程：
意图识别 → 输入校验 → 能力路由 → 算法调用 → 结果标准化 → 知识增强 → 判级 → 复核 → 报告。
字段全部 JSON 序列化安全（checkpointer 持久化 + 全程留痕），pydantic 模型转 dict 后入 state。
"""
from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    # ---- 任务身份 ----
    task_id: str
    trace_id: str
    mode: str  # single | joint

    # ---- 意图识别 / 输入 ----
    scene: str | None
    scenes: list[str]  # 用户声明的多场景列表（joint 提交），识别候选收窄依据
    candidate_scenes: list[dict[str, Any]]  # LLM 候选（规则命中时为空）
    input_uris: list[str]  # 原图落盘业务 URI
    auxiliary: dict[str, str]  # 配套输入（辐射值 CSV / 参考图）, name -> uri
    parameters: dict[str, Any]  # 透传算法参数

    # ---- 能力选择 ----
    selected_capability: dict[str, Any] | None  # 命中的能力清单（原 capability 字段）

    # ---- 执行结果 ----
    raw_result: dict[str, Any] | None  # 算法统一外壳
    standardized: list[dict[str, Any]]  # 标准化结果（缺陷/测量/分类）
    class_definitions: list[dict[str, Any]]  # 工具类别定义表（代码→中文名+真缺陷/虚假显示语义，报告与前端图例消费）
    grade_hint: dict[str, Any] | None  # 标准化派生判级线索（最高严重度/最低置信度/结果族）
    knowledge: list[dict[str, Any]]  # RAG 引用块（含解读）
    knowledge_prompt: str  # 检索完成后渲染的 default_kb 系统提示词（contexts + language）
    retrieval_trace: list[dict[str, Any]]  # 每轮检索留痕 {round, queries, hits, top_score, judged?}
    metric_explanations: str  # 指标解释 markdown（LLM 可选增强，空串=降级省略章节）
    error_report: dict[str, Any] | None  # 失败任务的异常原因报告引用（Task 6 启用）
    grade: dict[str, Any] | None  # 判级结论
    review: dict[str, Any] | None  # 人工复核决定（auto 通过时不含 reviewer）
    report: dict[str, Any] | None  # 报告引用

    # ---- 失败 ----
    error: dict[str, Any] | None  # {node, code, message}