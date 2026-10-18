"""任务相关请求/响应模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

TaskStatus = Literal[
    "pending", "running", "awaiting_review", "completed", "failed", "canceled"
]


class StepTraceItem(BaseModel):
    node: str
    message: str
    ts: datetime


class ArtifactRef(BaseModel):
    """推理产物引用（本地化后的业务 URI，前端可直接作 img src / 下载）。

    name 形如 artifact_original_image / artifact_result_image（InferTool 本地化
    时加 artifact_ 前缀）；mime 可为空（旧数据/未知类型），uri 必为
    /api/v1/tasks/{id}/files/{name}?kind=artifacts 形态。
    """

    name: str
    mime: str | None = None
    uri: str


class ReportRef(BaseModel):
    report_type: str = "agent_report"
    name: str
    uri: str
    version: str = "1.0.0"
    # AI 分析首段摘录（优化轮 D7：会话报告摘要卡）
    analysis_lead: str | None = None
    # 推理产物（原图/缺陷标注图等）：报告页「原图 vs 标注图」对比数据源
    artifacts: list[ArtifactRef] = Field(default_factory=list)


class TaskSummary(BaseModel):
    task_id: str
    scene: str | None
    mode: str
    status: TaskStatus
    capability_used: str | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    # 联合子任务回指父任务（优化轮 E2：列表「子」标签）
    parent_task_id: str | None = None
    # 参与推理的输入图像数（列表页「图像数量」列）
    image_count: int = 0


class TaskStats(BaseModel):
    """任务统计（GET /tasks/stats）：全量权威计数，非列表快照。"""

    total: int = 0
    today: int = 0  # 北京时间今日创建数
    pending: int = 0
    running: int = 0
    awaiting_review: int = 0
    completed: int = 0
    failed: int = 0
    canceled: int = 0


class TaskDetail(TaskSummary):
    trace_id: str | None = None
    # 原图业务 URI（本身即下载端点，前端不得再臆造文件名）
    input_uris: list[str] = Field(default_factory=list)
    steps: list[StepTraceItem] = []
    standardized: list[dict[str, Any]] = []
    # 工具类别定义表（class_id/class_name/description/is_true_defect）：
    # 报告「二 · 检测结果」类别图例与缺陷语义转译数据源
    class_definitions: list[dict[str, Any]] = Field(default_factory=list)
    grade: dict[str, Any] | None = None
    knowledge: list[dict[str, Any]] = []
    review: dict[str, Any] | None = None
    report: ReportRef | None = None
    error: dict[str, Any] | None = None
    # 联合父任务的子任务摘要列表（优化轮 E2 父子可视化）
    children: list[dict[str, Any]] = Field(default_factory=list)


class ReviewRequest(BaseModel):
    """人工复核（Human-in-the-loop 恢复指令）。"""

    action: Literal["confirm", "revise", "requeue", "reject"]
    opinion: str = Field(default="", description="复核意见，全程留痕")
    override_grade: dict[str, Any] | None = Field(
        default=None, description="revise 时覆盖的判级结论（level/summary 等）"
    )
    # 全量修订列表（决策：整表替换而非增量操作，幂等且无操作序语义）。
    # 条目沿用 DefectItem 形状 + 三个溯源标记：
    #   source="manual"（人工新增漏检）/ human_rejected=true（标记排除误检，
    #   条目保留供审计）/ revised={原值快照}（改类、改严重度、移框的 before 值）
    defects: list[dict[str, Any]] | None = Field(
        default=None, description="revise 时提交的全量缺陷修订列表（含溯源标记）"
    )


class SceneConfirmRequest(BaseModel):
    """场景人工确认（多候选歧义恢复指令）。"""

    scene: str = Field(description="人工确认的场景名，必须是待确认候选之一")
    opinion: str = Field(default="", description="确认说明，全程留痕")


class ReviewDecision(ReviewRequest):
    reviewer: str = "human"
    reviewed_at: datetime | None = None