"""能力注册中心领域模型（对齐 docs/a.md §3 CapabilityManifest）。

自 app/capabilities/manifests.py 迁入（a.md §13 models 层）。

字段命名对齐 a.md：
  scene / capability_type / adapter_type / endpoint / health_endpoint /
  capability_id / status(三态) / input_schema / output_schema / timeout_s / max_retry

平台扩展字段（default_params / input_required / families / max_images / display_name）
不再平铺在表列上，而是统一放入 meta（DB 侧 meta_json 列），便于表结构与 a.md 高度同构、
并支持 SQL 动态管理能力而不改表结构。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

CapabilityType = Literal["defect_detection", "measurement", "classification"]
AdapterType = Literal["http", "model_service", "local_bridge", "mock"]
StatusType = Literal["enabled", "disabled", "maintenance"]

# result 家族 -> 能力类型（normalizer 分流用，向后兼容勿删）
FAMILY_TO_TYPE: dict[str, CapabilityType] = {
    "defect": "defect_detection",
    "measurement": "measurement",
    "classification": "classification",
}


def _loads(value: Any) -> dict | None:
    """JSON 文本列 → dict：列空/非对象文本降级为 None，绝不让整行加载抛错。"""
    import json

    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


class CapabilityMeta(BaseModel):
    """平台扩展元数据（存 meta_json 列）：不入 a.md 15 列但推理/校验必需。"""

    families: list[str] = Field(default_factory=lambda: ["defect"])
    default_params: dict[str, Any] = Field(default_factory=dict)
    input_required: dict[str, Any] = Field(default_factory=dict)
    max_images: int = 50
    note: str = ""
    # 请求头注入：供需要认证头的真实工具（如热端部件要求 X-API-Key）
    headers: dict[str, str] = Field(default_factory=dict)


class CapabilityManifest(BaseModel):
    """算法能力描述（能力注册中心的领域对象，纯 DB 驱动加载）。"""

    capability_id: str = Field(description="能力唯一 ID（人工注册键，如 borescope_defect_detection_v1）")
    tool_name: str = Field(description="算法工具名称（Tool 挂载名 detect/<tool_name>）")
    scene: str = Field(description="适用检测场景编码（路由键）")
    capability_type: CapabilityType = Field(default="defect_detection")
    adapter_type: AdapterType = Field(default="http")
    endpoint: str = Field(default="", description="推理服务地址（http 用）")
    health_endpoint: str | None = Field(None)
    tool_version: str = "1.0.0"
    model_version: str = "1.0.0"
    api_version: str = "v1"
    input_schema: dict | None = Field(None, description="输入参数结构（JSON Schema）")
    output_schema: dict | None = Field(None, description="输出结果结构（JSON Schema）")
    timeout_s: int = Field(default=60)
    max_retry: int = Field(default=2)
    status: StatusType = Field(default="enabled", description="启停控制：enabled/disabled/maintenance")
    description: str | None = Field(None)
    # 规范 §9：模型权重 SHA256（准入时校验与申报一致，防篡改/版本混淆）；可选，模型文件到位后回填
    model_weights_sha256: str | None = Field(None)
    # 平台扩展（meta_json 列）
    families: list[str] = Field(default_factory=lambda: ["defect"])
    default_params: dict[str, Any] = Field(default_factory=dict)
    input_required: dict[str, Any] = Field(default_factory=dict)
    max_images: int = 50
    note: str = ""
    # 请求头注入：供需要认证头的真实工具（如热端部件要求 X-API-Key）
    headers: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_record(cls, rec: Any) -> "CapabilityManifest":
        """从能力表记录（CapabilityRecord）构造，meta_json 反序列化进扩展字段。"""
        import json

        meta: CapabilityMeta = CapabilityMeta.model_validate(
            json.loads(rec.meta_json) if rec.meta_json else {}
        )
        return cls(
            capability_id=rec.capability_id,
            tool_name=rec.tool_name,
            scene=rec.scene,
            capability_type=rec.capability_type,
            adapter_type=rec.adapter_type,
            endpoint=rec.endpoint or "",
            health_endpoint=rec.health_endpoint,
            tool_version=rec.tool_version,
            model_version=rec.model_version,
            api_version=rec.api_version,
            input_schema=_loads(getattr(rec, "input_schema", None)),
            output_schema=_loads(getattr(rec, "output_schema", None)),
            timeout_s=rec.timeout_s,
            max_retry=rec.max_retry,
            status=rec.status,
            description=rec.description,
            model_weights_sha256=getattr(rec, "model_weights_sha256", None),
            families=meta.families,
            default_params=meta.default_params,
            input_required=meta.input_required,
            max_images=meta.max_images,
            note=meta.note,
            headers=meta.headers,
        )

    @property
    def base_url(self) -> str:
        """兼容别名：旧 http_adapter 用 base_url 读服务地址。"""
        return self.endpoint

    @property
    def metric_family(self) -> str:
        """normalizer 分流：capability_type -> result 家族（defect/measurement/classification）。"""
        for family, cap_type in FAMILY_TO_TYPE.items():
            if cap_type == self.capability_type:
                return family
        return "defect"

    @property
    def enabled(self) -> bool:
        return self.status == "enabled"


class CapabilitySummary(BaseModel):
    """能力列表/健康巡检的对外摘要（scene 命名对齐）。"""

    capability_id: str
    tool_name: str
    tool_version: str
    model_version: str
    display_name: str
    scene: str
    capability_type: CapabilityType
    adapter_type: AdapterType
    status: StatusType
    note: str


class CapabilityHealth(BaseModel):
    tool_name: str
    healthy: bool
    message: str
    latency_ms: int | None = None


# ======================= 写侧（批次 3 / 决策 27–50） =======================
# 决策 11：对外模型统一收敛在 models 层，api/schemas/capability.py 只做转发别名。

# PATCH 可写字段白名单（决策 36：capability_id / tool_name 创建后全锁，不在表内）
WRITABLE_FIELDS: tuple[str, ...] = (
    "scene",
    "capability_type",
    "adapter_type",
    "endpoint",
    "health_endpoint",
    "tool_version",
    "model_version",
    "api_version",
    "input_schema",
    "output_schema",
    "timeout_s",
    "max_retry",
    "description",
    "model_weights_sha256",
    "meta",
)


class CapabilityCreateBody(BaseModel):
    """登记新能力（决策 27/37/46）。status 不在表单内：新行先以 disabled 落库，
    干跑门禁通过才允许 enabled（决策 27）。"""

    capability_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(min_length=1, max_length=128)
    scene: str = Field(description="场景键，白名单由后端校验（源自 scene_router.SCENE_KEYWORDS）")
    endpoint: str = Field(min_length=1, max_length=512)
    tool_version: str = Field(min_length=1, max_length=64)
    model_version: str = Field(min_length=1, max_length=64)
    capability_type: CapabilityType = "defect_detection"
    adapter_type: AdapterType = "http"
    health_endpoint: str | None = Field(default=None, max_length=512)
    api_version: str = Field(default="v1", max_length=32)
    input_schema: dict | None = None
    output_schema: dict | None = None
    timeout_s: int = Field(default=60, ge=1, le=600)
    max_retry: int = Field(default=2, ge=0, le=5)
    description: str | None = None
    model_weights_sha256: str | None = Field(default=None, max_length=64)
    meta: dict = Field(default_factory=dict)
    status: StatusType = "disabled"
    change_reason: str = Field(min_length=1, max_length=100)
    operator: str | None = Field(default=None, max_length=64)
    expected_snapshot: str | None = Field(
        default=None, description="完整试运行回执；端点/请求头与提交值不一致时必填"
    )


class CapabilityPatchBody(BaseModel):
    """字段级 patch（决策 39/42/46）：未出现的字段保留原值，显式 null 清空。"""

    expected_version: int = Field(description="详情端点下发的乐观锁水位")
    change_reason: str = Field(min_length=1, max_length=100)
    operator: str | None = Field(default=None, max_length=64)
    expected_snapshot: str | None = None

    scene: str | None = None
    capability_type: CapabilityType | None = None
    adapter_type: AdapterType | None = None
    endpoint: str | None = Field(default=None, max_length=512)
    health_endpoint: str | None = Field(default=None, max_length=512)
    tool_version: str | None = Field(default=None, max_length=64)
    model_version: str | None = Field(default=None, max_length=64)
    api_version: str | None = Field(default=None, max_length=32)
    input_schema: dict | None = None
    output_schema: dict | None = None
    timeout_s: int | None = Field(default=None, ge=1, le=600)
    max_retry: int | None = Field(default=None, ge=0, le=5)
    description: str | None = None
    model_weights_sha256: str | None = Field(default=None, max_length=64)
    meta: dict | None = None


class CapabilityStatusBody(BaseModel):
    """启停（决策 45）：独立端点，不复用编辑表单。"""

    status: StatusType
    change_reason: str = Field(min_length=1, max_length=100)
    operator: str | None = Field(default=None, max_length=64)


class CapabilityDetail(BaseModel):
    """详情回显（决策 43：headers 只回掩码，明文不出读方向）。"""

    capability_id: str
    tool_name: str
    scene: str
    capability_type: CapabilityType
    adapter_type: AdapterType
    endpoint: str
    health_endpoint: str | None
    tool_version: str
    model_version: str
    api_version: str
    input_schema: dict | None
    output_schema: dict | None
    timeout_s: int
    max_retry: int
    status: StatusType
    description: str | None
    model_weights_sha256: str | None
    meta: dict
    headers_masked: dict[str, str]
    change_log: list[dict] = Field(default_factory=list)
    version: int
    snapshot: str
    editable: bool = True


class DryRunBody(BaseModel):
    """干跑沙箱（决策 34）：不落库，只用请求体描述的临时端点跑三段连通性。"""

    endpoint: str = Field(min_length=1, max_length=512)
    health_path: str = Field(default="/health", max_length=256)
    inference_path: str = Field(default="/api/v1/inference", max_length=256)
    headers: dict[str, str] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    image_field: str = Field(default="image", max_length=64)
    image_path: str = Field(
        default="", description="仓库内样例图相对路径（限 local-tools/ 与 data/ 之下）"
    )
    capability_type: CapabilityType = "defect_detection"
    timeout_s: int = Field(default=60, ge=1, le=600)


class DryRunStep(BaseModel):
    name: str
    ok: bool
    message: str
    latency_ms: int | None = None


class DryRunResult(BaseModel):
    passed: bool
    steps: list[DryRunStep]
    sample: dict | None = None  # 归一后的 canonical 外壳，可直接落 output_schema
    gate_errors: list[str] = Field(default_factory=list)
    snapshot: str = ""

