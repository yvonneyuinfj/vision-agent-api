"""ReAct 轨状态：AgentState 超集 + 对话/循环通道。

- messages：add_messages reducer（append 语义），承载智能体完整内心戏；
  完整轨迹同时留在检查点（决策 12），结构化摘要走 append_step。
- loop_count / capability_calls：预算缰绳（决策 11/5），由节点显式管理。
- submitted / submit_payload：出口安检交接——工具层只收集提交意图，
  判级终裁与复核门一律由出口节点运行时执行（决策 7 读法 A）。
"""
from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from app.agents.state import AgentState


class ReactState(AgentState, total=False):
    # ---- ReAct 对话 ----
    messages: Annotated[list[AnyMessage], add_messages]

    # ---- 循环预算 ----
    loop_count: int  # agent 节点已执行轮数（上限 settings.react_max_turns）
    capability_calls: int  # invoke_capability 累计次数（上限 1+2 自救，决策 5）
    scenes_tried: list[dict[str, Any]]  # 已检测场景历史 [{scene, ok, defect_count, summary}]（状态栏数据源）

    # ---- 出口交接 ----
    submitted: bool
    submit_payload: dict[str, Any] | None  # {refused, reason} | {defects, summary, analysis}

    # ---- 联合质检（mode=joint，决策 10：父子任务 + 子任务落定事件注入） ----
    children_launched: dict[str, str]  # scene -> 子任务 task_id
    await_children_requested: bool  # await_children 工具登记等待（路由进 await 分支）
    children_settled: int  # 唤醒后落定子任务数（出口完备性凭据）
