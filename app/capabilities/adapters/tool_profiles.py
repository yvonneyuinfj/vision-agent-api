"""真实工具响应 profile：把外壳偏离规范的交付工具归一为平台 canonical 形态。

背景：《统一API接入规范》§7 要求 {code,message,task_id,trace_id,tool,input,performance,result}
外壳（mock 服务即规范形态）；首个真实交付工具（荧光渗透 blade_defect_detector，
thread-defect-api-20260910）返回 {code,message,trace_id,task_id,data:{…}} 偏差外壳，
缺陷列表（data.detections）与产物（data.artifacts）均嵌在 data 内，且字段名不同
（class_name/bbox vs defect_type/location）。若不转换，normalize 节点读不到
result.defects，会把不合格件静默判为"未发现异常"——联调适配 #1 的修复点。

设计（本地已跑通验证，2026-09-15）：
- 不改交付件代码，平台侧单点归一：http_adapter._parse_response 应用 apply_tool_profile；
- profile 以工具自报 data.tool_name 为键自动识别；顶层已有 result（规范形态）
  或未知工具一律原样透传，对 mock 与后续规范工具零影响；
- 转换无损：原 data 整体保留（追溯），仅追加 canonical 顶层键 result/artifacts/performance；
- 语义映射：BP/SP（is_true_defect）→ defects[]（severity 不提供不伪造，normalizer 契约）；
  FI（虚假荧光显示）→ false_indication_count 计数，不混入真缺陷；
  class_definitions 类别定义表清洗后随 result 透传（报告与前端图例消费：
  类别代码 → 中文名 + 真缺陷/虚假显示语义，正文不再罗列 bbox 坐标）；
  business_decision unqualified/qualified → status abnormal/normal（对齐 mock 语义，
  grading 节点与报告链路直接消费）。

新增真实工具若外壳偏差：在 PROFILES 注册一个转换函数即可，推理链路零改动。
"""
from __future__ import annotations

from typing import Any, Callable

__all__ = [
    "PROFILES",
    "apply_tool_profile",
    "transform_blade_defect_detector",
    "transform_borescope",
    "transform_metallography",
    "transform_dr_structure",
]


def _as_bbox(value: Any) -> list[float] | None:
    """工具 bbox → 平台契约四元组；非法形态返回 None（前端按无框处理，不绘制脏框）。"""
    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            return [float(v) for v in value]
        except (TypeError, ValueError):
            return None
    return None


def _clean_class_definitions(raw: Any) -> list[dict[str, Any]]:
    """工具 class_definitions → 平台清洗形态（只留报告/前端消费的语义字段）。"""
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for d in raw:
        if not isinstance(d, dict):
            continue
        out.append(
            {
                "class_id": d.get("class_id"),
                "class_name": str(d.get("class_name") or ""),
                "description": str(d.get("description") or ""),
                "is_true_defect": bool(d.get("is_true_defect")),
            }
        )
    return out


def transform_blade_defect_detector(envelope: dict[str, Any]) -> dict[str, Any]:
    """荧光渗透 blade_defect_detector：data 外壳 → 平台 canonical（无损增补）。"""
    data = envelope.get("data") or {}
    detections = data.get("detections") or []

    # 类别定义表清洗后随 result 透传（报告「二 · 检测结果 / 三 · 缺陷逐项深度分析」
    # 与前端类别图例消费：类别代码 → 中文名 + 真缺陷/虚假显示语义）
    definitions = _clean_class_definitions(data.get("class_definitions"))
    def_by_id: dict[int, dict[str, Any]] = {}
    for d in definitions:
        try:
            def_by_id[int(d["class_id"])] = d
        except (TypeError, ValueError):
            continue

    # 真缺陷 class_id 集合：优先采用工具自带 class_definitions（防硬编码漂移），
    # 缺省回退交付契约 {0:BP, 1:SP}（2=FI 虚假显示不计真缺陷）
    true_defect_ids = {0, 1}
    declared = {cid for cid, d in def_by_id.items() if d["is_true_defect"]}
    if declared:
        true_defect_ids = declared

    defects: list[dict[str, Any]] = []
    false_indications = 0
    for det in detections:
        if not isinstance(det, dict):
            continue
        try:
            class_id = int(det.get("class_id", -1))
        except (TypeError, ValueError):
            class_id = -1
        if class_id not in true_defect_ids:
            false_indications += 1
            continue
        conf = det.get("confidence")
        class_name = det.get("class_name") or "unknown"
        # 中文类别描述：detection 自带优先，缺失回退类别定义表（class_definitions 兜底）
        class_cn = str(det.get("class_description") or "") or def_by_id.get(class_id, {}).get("description", "")
        description = f"荧光渗透显示 {class_name}"
        # 括号内合并「中文类别描述 + 置信度」，避免产出 `（x），置信度 y` 的断裂括号
        paren = "，".join(
            part
            for part in (
                class_cn,
                f"置信度 {float(conf):.2f}" if isinstance(conf, (int, float)) else "",
            )
            if part
        )
        if paren:
            description += f"（{paren}）"
        defects.append(
            {
                "defect_id": f"D{len(defects) + 1:03d}",
                "defect_type": class_name,
                "defect_name_cn": det.get("class_description") or "",
                "confidence": conf,
                "severity": None,  # 工具不提供严重度——不伪造（defect_normalizer 契约）
                # 平台 location 契约 {type:"bbox", bbox:[x1,y1,x2,y2]}（对齐 mock 服务）：
                # 前端 DefectOverlay / 报告矩形框按该形态绘制，只给 bbox_xyxy 会导致
                # 「算法标注图」一个框都画不出、与原图完全一致（2026-09-15 联调回归）。
                "location": {
                    "type": "bbox",
                    "bbox": _as_bbox(det.get("bbox")),
                    "bbox_xyxy": det.get("bbox"),  # 工具原字段名，保留供追溯
                },
                "description": description,
            }
        )

    decision = data.get("business_decision")
    status = {"unqualified": "abnormal", "qualified": "normal"}.get(str(decision))
    if status is None:
        status = "abnormal" if data.get("is_defective") else "normal"

    if defects:
        summary = f"检出 {len(defects)} 处真缺陷"
        if false_indications:
            summary += f"（另有 {false_indications} 处虚假荧光显示，不计入）"
    elif false_indications:
        summary = f"未检出真缺陷（{false_indications} 处虚假荧光显示）"
    else:
        summary = "未发现明显异常"

    artifacts = [
        {
            "name": art.get("artifact_type") or f"artifact_{index}",
            "mime_type": art.get("media_type"),
            "uri": art.get("uri") or "",
        }
        for index, art in enumerate(data.get("artifacts") or [])
        if isinstance(art, dict) and art.get("uri")
    ]

    out = dict(envelope)  # 浅拷贝：原响应 data 整体保留，供报告/追溯
    out["result"] = {
        "status": status,
        "summary": summary,
        "defects": defects,
        # 工具业务字段透传（报告/追溯消费，不参与判级）
        "business_decision": decision,
        "model_event_count": data.get("model_event_count"),
        "true_defect_count": data.get("true_defect_count"),
        "false_indication_count": false_indications,
        "class_definitions": definitions,
        "tool_name": data.get("tool_name"),
        "model_version": data.get("model_version"),
        "part_id": data.get("part_id"),
        "device_id": data.get("device_id"),
        "image_name": data.get("image_name"),
    }
    if artifacts:
        out["artifacts"] = artifacts
    if "performance" not in out:
        out["performance"] = {"processing_time_ms": data.get("inference_time_ms")}
    return out


# ============================================================
# 2026-09-21 新增真实工具归一（from 20260921 交付批次）
# ------------------------------------------------------------
# 平台契约仍是 {…, result:{status, defects[]}} 缺陷检测形态；本轮 4 个工具外壳各异：
#   · borescope（18014 孔探）：扁平顶层 {code,tool,detections[]}，无 result → 由
#     detections[] 折算为缺陷列表（真检出几何框）
#   · hot-end（18015 热端）：顶层 result 已是规范形态（status+defects）→ 原样透传
#   · metallography（18013 金相）：顶层 result.status="success" + instances[] 为 γ′相
#     定量分析（非缺陷检测）→ 归为 normal 分析结论，定量指标随 result 进报告
#   · dr_structure（18011 X光/DR）：data.result 为结构/间隙分析 → 归为 normal 分析结
#     论，结构结果与产物随 result 进报告
# 语义约定（2026-09-21 决策）：孔探/热端按缺陷检测处理；X光/金相按“分析上报”处理，
# 不伪造成标量缺陷，避免平台/复核/报告误判。
# ============================================================


def _as_canonical_defects(detections: Any, *, tool_label: str) -> list[dict[str, Any]]:
    """从工具检出列表折算为平台 result.defects[]（location 用规范 bbox 四元组）。"""
    out: list[dict[str, Any]] = []
    if not isinstance(detections, list):
        return out
    for index, det in enumerate(detections):
        if not isinstance(det, dict):
            continue
        dtype = (
            det.get("defect_type")
            or det.get("category")
            or det.get("class_name")
            or "unknown"
        )
        name_cn = det.get("defect_name_cn") or det.get("class_description") or ""
        conf = det.get("confidence")
        loc = det.get("location") or {}
        bbox = loc.get("bbox") if isinstance(loc, dict) else None
        if not bbox:
            bbox = det.get("bbox")
        out.append(
            {
                "defect_id": f"D{index + 1:03d}",
                "defect_type": dtype,
                "defect_name_cn": name_cn,
                "confidence": conf,
                "severity": det.get("severity"),
                "location": {
                    "type": "bbox",
                    "bbox": _as_bbox(bbox),
                },
                "description": det.get("description") or f"检测到疑似{dtype}区域",
            }
        )
    return out


def transform_borescope(envelope: dict[str, Any]) -> dict[str, Any]:
    """孔探（borescope）：扁平 detections[] → 平台 result.defects[]（几何框缺陷）。"""
    detections = envelope.get("detections") or []
    defects = _as_canonical_defects(
        detections, tool_label=str(envelope.get("tool") or "borescope")
    )
    component = envelope.get("component") or envelope.get("component_name") or ""
    if defects:
        summary = f"检出 {len(defects)} 处疑似缺陷" + (f"（{component}）" if component else "")
        status = "abnormal"
    else:
        summary = "未发现明显异常" + (f"（{component}）" if component else "")
        status = "normal"
    out = dict(envelope)  # 浅拷贝，扁平原响应保留（追溯）
    out["result"] = {
        "status": status,
        "summary": summary,
        "defects": defects,
        "component": component,
        "tool_name": envelope.get("tool") or envelope.get("tool_name"),
        "model_version": envelope.get("model_version"),
    }
    return out


def transform_metallography(envelope: dict[str, Any]) -> dict[str, Any]:
    """金相（metallography）：定量分析（instances/占比）→ 归为 normal 分析结论。
    非缺陷检测，不伪造成标量缺陷；γ′相/γ相占比、颗粒统计随 result 进报告。"""
    result = envelope.get("result") or {}
    if not isinstance(result, dict):
        result = {}
    out = dict(envelope)
    new_result = dict(result)
    ga = result.get("gamma_area_fraction")
    gpa = result.get("gamma_prime_area_fraction")
    part = result.get("particle_number")
    mean_size = result.get("gamma_prime_mean_size_px")
    bits = []
    if ga is not None:
        bits.append(f"γ相面积占比 {ga:.3f}")
    if gpa is not None:
        bits.append(f"γ′相面积占比 {gpa:.3f}")
    if part is not None:
        bits.append(f"γ′颗粒数 {part}")
    if mean_size is not None:
        bits.append(f"γ′平均尺寸 {mean_size:.1f}px")
    new_result["status"] = "normal"  # 分析类工具：无标量缺陷，不判 abnormal
    new_result["summary"] = "金相定量分析：" + ("，".join(bits) if bits else "完成")
    new_result["defects"] = []  # 补齐空缺陷列表，下游 normalize/判级/报告零中断
    out["result"] = new_result
    return out


def transform_dr_structure(envelope: dict[str, Any]) -> dict[str, Any]:
    """X光/DR（dr_structure）：结构定位/间隙/转速分析 → 归为 normal 分析结论。
    data.result 为结构分析报告（非标量缺陷）；结构类型、帧数、产物随 result 进报告。"""
    data = envelope.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    sr = data.get("structure_results") or []
    if not isinstance(sr, list):
        types = []
    else:
        types = [str(s.get("structure_type")) for s in sr if isinstance(s, dict) and s.get("structure_type")]
    frames = data.get("valid_frame_count") or data.get("frame_count")
    out = dict(envelope)  # 浅拷贝：原 data 保留（报告/追溯消费结构+间隙全量）
    result = {
        "status": "normal",  # DR 是结构/间隙分析，非缺陷检测，不判 abnormal
        "summary": f"DR 结构分析：定位结构 {len(types)} 个（{('、'.join(types)) if types else '—'}）"
        + (f"，有效帧 {frames}" if frames is not None else ""),
        "defects": [],
        "structure_types": types,
        "frame_count": frames or data.get("frame_count"),
        "tool_name": "dr_structure",
        "model_version": data.get("model_version") or envelope.get("api_version"),
    }
    out["result"] = result
    artifacts = data.get("artifacts")
    if artifacts and not out.get("artifacts"):
        out["artifacts"] = artifacts
    return out


PROFILES: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "blade_defect_detector": transform_blade_defect_detector,
}


def apply_tool_profile(envelope: Any) -> Any:
    """自动识别并应用工具 profile；规范形态（顶层 result 且 status 缺陷语义）原样返回。"""
    if not isinstance(envelope, dict):
        return envelope

    top_result = envelope.get("result")
    # 热端（hot-end）：顶层 result 已是规范缺陷形态（status abnormal/normal + defects）→ 原样透传
    if isinstance(top_result, dict) and isinstance(top_result.get("defects"), list):
        if str(top_result.get("status")) in ("abnormal", "normal"):
            return envelope

    # 金相（metallography）：顶层 result.status=success + instances[] 定量 → 归一为分析结论
    if isinstance(top_result, dict) and isinstance(top_result.get("instances"), list):
        return transform_metallography(envelope)

    # 孔探（borescope）：扁平顶层 detections[]，无 result → 折算为缺陷
    if envelope.get("tool") == "borescope" or "borescope" in str(envelope.get("tool_name", "")).lower():
        if isinstance(envelope.get("detections"), list):
            return transform_borescope(envelope)

    # X光/DR：data.result 结构分析 → 归一为分析结论
    data = envelope.get("data")
    if isinstance(data, dict) and isinstance(data.get("result"), dict) and (
        "structure_type" in data.get("result") or "structure_results" in data
    ):
        return transform_dr_structure(envelope)

    # 荧光（blade_defect_detector）：data 外壳 + data.tool_name 命中已有 profile
    if isinstance(data, dict):
        transform = PROFILES.get(str(data.get("tool_name") or ""))
        if transform is not None:
            return transform(envelope)

    return envelope
