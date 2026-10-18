"""能力写侧门禁与掩码（决策 29/31/37/40/43/46 的常量与纯函数落点）。

分层：本模块只做**与 IO 无关**的校验/合并/掩码，供 api/v1/capability 写端点与
sync 护栏复用；DB 读写仍在 CapabilityRepo，HTTP 探活仍在 dry-run 端点。
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from app.models.capability import CapabilityType
from app.services.scene_catalog import SCENE_CATALOG

# 决策 37（决策 52 回写）：scene 白名单＝**场景目录**，可登记扩展。
# 名单外的能力在 registry 里 enabled 且健康，却永不被 lookup() 命中（"死能力"），
# 故仍必须在写入口 400；但新增场景只需进目录，不再改这张常量。
ALLOWED_SCENES: tuple[str, ...] = tuple(SCENE_CATALOG)

CAPABILITY_TYPES: tuple[CapabilityType, ...] = (
    "defect_detection",
    "measurement",
    "classification",
)
ADAPTER_TYPES: tuple[str, ...] = ("http", "model_service", "local_bridge", "mock")

# 决策 36：创建后全锁的身份列（PATCH 直接拒绝，换身份＝新建 + 停用旧行）
IMMUTABLE_FIELDS: frozenset[str] = frozenset({"capability_id", "tool_name"})

# meta 里表单不呈现的死键（决策 40：零运行时消费者）
DEAD_META_KEYS: frozenset[str] = frozenset({"families", "max_images"})

# 决策 29：期望响应样例的硬门禁字段清单（后端常量，不给表单复选框）
SAMPLE_REQUIRED_TOP_LEVEL: tuple[str, ...] = ("code", "result")
ARTIFACT_REQUIRED_KEYS: tuple[str, ...] = ("name", "uri")

# 决策 31：门禁分支由 capability_type 推导（真实工具行同步修正，避免说谎行）
RESULT_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "defect_detection": ("status", "defects"),
    "measurement": ("status", "summary"),
    "classification": ("status", "summary"),
}

CHANGE_LOG_KEEP = 20  # meta_json 是 TEXT 列，留痕取最近 N 条有界

_HEADERS_KEY = "headers"


def validate_meta(meta: dict[str, Any]) -> list[str]:
    """合并后的 meta 必须能被 CapabilityMeta 装载（决策 41 的"便宜且确定"检查）。

    不校验的后果是真实的：`registry.refresh()` 对加载失败的行只 log 后 **静默跳过**
    （registry.py:73），页面上一行合法 JSON 会让工具从 ToolRegistry 里凭空消失。
    """
    from app.models.capability import CapabilityMeta

    try:
        CapabilityMeta.model_validate(meta)
    except Exception as exc:  # noqa: BLE001 - pydantic 文案就是给运维看的
        return [f"meta 无法装载（该能力将无法被路由加载）: {exc}"[:400]]
    return []


def validate_response_sample(sample: Any, capability_type: str) -> list[str]:
    """校验人工编辑的期望响应样例，返回缺陷清单（空列表＝门禁通过）。"""
    if not isinstance(sample, dict):
        return ["样例必须是 JSON 对象（顶层为 {…}）"]

    errors: list[str] = []
    if "code" not in sample:
        errors.append("缺少顶层字段 code")
    elif sample["code"] != 0:
        errors.append(f"code 必须为 0（样例实测 {sample['code']!r}）")

    result = sample.get("result")
    if not isinstance(result, dict):
        errors.append("缺少 result 对象（平台归一后的结论容器）")
        return errors

    required = RESULT_REQUIRED_FIELDS.get(capability_type, RESULT_REQUIRED_FIELDS["defect_detection"])
    for field in required:
        if field not in result:
            errors.append(f"result 缺少 {field}")
    if "defects" in required and "defects" in result and not isinstance(result["defects"], list):
        errors.append("result.defects 必须是数组")

    artifacts = sample.get("artifacts")
    if artifacts is not None:
        if not isinstance(artifacts, list):
            errors.append("artifacts 必须是数组")
        else:
            for index, art in enumerate(artifacts):
                if not isinstance(art, dict):
                    errors.append(f"artifacts[{index}] 必须是对象")
                    continue
                missing = [k for k in ARTIFACT_REQUIRED_KEYS if not art.get(k)]
                if missing:
                    errors.append(f"artifacts[{index}] 缺少 {'/'.join(missing)}")
    return errors


def mask_headers(headers: Any) -> dict[str, str]:
    """决策 43：读方向只回「键名 + 末 4 位」，明文只存在于写入方向与真实调用。"""
    if not isinstance(headers, dict):
        return {}
    masked: dict[str, str] = {}
    for key, value in headers.items():
        text = str(value)
        masked[str(key)] = f"****{text[-4:]}" if len(text) >= 8 else "****"
    return masked


def merge_meta_patch(raw: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """meta 子键级浅合并（决策 40），作用于**原始 dict**（不过 pydantic 往返，未知键不丢）。

    - 未出现的子键：保留原值
    - 出现的子键：整块替换；值为 None：删除该子键
    - 例外 headers（决策 43）：逐键合并，键值为 None 即删除该请求头
    """
    merged = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    for key, value in patch.items():
        if key == _HEADERS_KEY:
            current = merged.get(_HEADERS_KEY)
            headers = dict(current) if isinstance(current, dict) else {}
            if not isinstance(value, dict):
                raise ValueError("meta.headers 必须是对象")
            for header_name, header_value in value.items():
                if header_value is None:
                    headers.pop(str(header_name), None)
                else:
                    headers[str(header_name)] = str(header_value)
            merged[_HEADERS_KEY] = headers
            continue
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def append_change_log(
    meta: dict[str, Any],
    *,
    reason: str,
    operator: str | None,
    action: str,
    changed_fields: list[str],
) -> dict[str, Any]:
    """决策 46：最小留痕、零 DDL——变更原因必填 + 自报操作人，写进 meta.change_log。

    自报、不认证（与 task.py reviewer 同口径）；身份认证与前后值审计属 G4-01。
    """
    entry = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "action": action,
        "operator": operator or "未填",
        "reason": reason,
        "fields": changed_fields,
    }
    log = meta.get("change_log")
    history = list(log) if isinstance(log, list) else []
    history.insert(0, entry)
    meta["change_log"] = history[:CHANGE_LOG_KEEP]
    return meta
