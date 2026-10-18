"""能力注册中心种子脚本 + 真实图片联调（纯 DB 驱动的人工注册入口，决策 3/C + 4/B）。

用法：
    # 默认：阶段一（能力注册）+ 阶段二（data/inputs/_upload 现有图片提交为真实质检任务跑全流程）
    uv run python -m scripts.sync_capabilities

    # 仅注册能力，不提交图片任务
    uv run python -m scripts.sync_capabilities --no-tasks

    # 重置已存在行（决策 32 护栏逃生门：会覆盖 UI/DBA 人工登记的 endpoint/headers/类型）
    uv run python -m scripts.sync_capabilities --no-tasks --force

    # 注册能力 + 只打印待分析图片与场景映射，不真正提交
    uv run python -m scripts.sync_capabilities --dry-run

    # 生产（MySQL；需已执行 scripts/sql/init.sql 建表）
    DATABASE_URL=mysql+aiomysql://user:pass@host:3306/quality_inspection \
        uv run python -m scripts.sync_capabilities
    # mock 以单进程多域形态运行（系统六场景共享 8002），启动方式见根 README mock 段

阶段一语义（能力注册）：
    - 来源：mock_service/main.py --identity 子进程自报身份（tool_name/tool_version/
      model_version/api_version + defect_domains 域名声明；单进程多域，不按工具名区分）
      + 本脚本内置的映射表（scene/capability_type/endpoint…，人工维护，不自动扫描目录）
    - 幂等：新行 insert；已存在行走**列级 patch**（决策 32/39 护栏）——
      endpoint / health_endpoint / capability_type / meta.headers 属于人工登记事实，
      sync 不覆盖；内置表与既有行 endpoint 不同时，mock 自报的版本号也不写入
      （真工具的行被洗成 tool_version=0.1.0 就是这么来的）；显式 --force 才全字段重置
    - 纯人工动作：注册/启停/维护开关由 DBA/运维执行本节，平台启动零文件依赖

阶段二语义（真实图片联调播种）：
    - 前置清理：先清空任务表及既有产物（任务行 / data/reports / data/artifacts /
      LangGraph 检查点线程），保证每次播种的演示数据纯净；
      清理发生在 mock 健康预检通过之后（服务不可用时不无谓清空）
    - 图片来源：data/inputs/_upload 现有图片（直接复用，绝不生成合成图）
      * classX_*（X=1~4）：试样分类图文档素材 —— 显式忽略，不提交
      * classify_*  → fracture（断口失效形貌分类：解理/韧窝/沿晶等）
      * yingguang_* → fpi（荧光渗透检测）
      * 其余命名（hongwai/ir/kongtan/csv 等）本轮不处理
    - 场景计划表（SCENARIO_PLAN）：人工维护的确定性演示形态，播种目标——
      * 状态多样：completed / awaiting_review / failed 三态各至少一个
      * 缺陷多样：不允许全部任务无缺陷
      * 有缺陷必须带矩形框标注：mock 输出 location.bbox + 标注图
        （manifest 默认 return_annotated=true，标注图生成依赖 Pillow），
        报告同步呈现矩形框坐标与标注图引用
    - 直连编排：复用 AppContainer 构建 TaskService（与生产同一张图 + 同一套
      checkpointer），不走 HTTP 层；推理依赖 mock 服务在 8002，提交前预检 /health
    - 全流程：prepare → run_task → 复核门按场景计划自动确认或停留演示 → 报告落盘
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ---------- 内置映射表（人工维护：新增算法工具在此登记） ----------
# 键：tool_name（须与 mock_service/main.py 的 TOOL_NAME 一致）
BUILTIN_CAPABILITIES: dict[str, dict] = {
    "borescope_defect_detection": {
        "capability_id": "borescope_defect_detection_v1",
        "scene": "borescope",
        "capability_type": "defect_detection",
        "adapter_type": "http",
        "endpoint": "http://127.0.0.1:8002",
        "health_endpoint": "http://127.0.0.1:8002/health",
        "description": "内窥镜拍到的叶片画面，找裂纹、烧蚀、腐蚀与孔隙并框出位置（当前为联调模拟结果，真实模型未接入）",
        "meta": {
            "families": ["defect"],
            "default_params": {
                "confidence_threshold": 0.5,
                "max_defects": 1,
                "return_annotated": True,
            },
            "input_required": {"images": 1, "image_field": "image"},
            "max_images": 10,
            "note": "联调用 Mock 服务（mock_service/main.py，单进程多域共享 8002）",
        },
    },
    "fracture_defect_detection": {
        "capability_id": "fracture_defect_detection_v1",
        "scene": "fracture",
        "capability_type": "defect_detection",
        "adapter_type": "http",
        "endpoint": "http://127.0.0.1:8002",
        "health_endpoint": "http://127.0.0.1:8002/health",
        "description": "断口电镜形貌，凭韧窝、解理等特征推断失效方式（当前为联调模拟结果，真实模型未接入）",
        "meta": {
            "families": ["defect"],
            "default_params": {
                "confidence_threshold": 0.5,
                "max_defects": 1,
                "return_annotated": True,
            },
            "input_required": {"images": 1, "image_field": "image"},
            "max_images": 10,
            "note": "联调用 Mock 服务（mock_service/main.py，单进程多域共享 8002）",
        },
    },
    "metallography_defect_detection": {
        "capability_id": "metallography_defect_detection_v1",
        "scene": "metallography",
        # 决策 31：金相交付的是 γ′相定量分析（measurement），不是缺陷检出。
        # 曾登记为 defect_detection 属"说谎行"——门禁（决策 29/30）按 capability_type
        # 分支校验样例，类型错了门禁就形同虚设。
        "capability_type": "measurement",
        "adapter_type": "http",
        "endpoint": "http://127.0.0.1:8002",
        "health_endpoint": "http://127.0.0.1:8002/health",
        "description": "抛光腐蚀后的显微组织，量出 γ/γ′ 相占比、颗粒数与平均尺寸（定量结论，不报缺陷个数；当前为联调模拟结果，真实模型未接入）",
        "meta": {
            "families": ["defect"],
            "default_params": {
                "confidence_threshold": 0.5,
                "max_defects": 1,
                "return_annotated": True,
            },
            "input_required": {"images": 1, "image_field": "image"},
            "max_images": 10,
            "note": "联调用 Mock 服务（mock_service/main.py，单进程多域共享 8002）",
        },
    },
    "infrared_defect_detection": {
        "capability_id": "infrared_defect_detection_v1",
        "scene": "infrared",
        "capability_type": "defect_detection",
        "adapter_type": "http",
        "endpoint": "http://127.0.0.1:8002",
        "health_endpoint": "http://127.0.0.1:8002/health",
        "description": "热像图先看温度分布是否异常，再检出表面缺陷（自动做分辨率增强；当前为联调模拟结果，真实模型未接入）",
        "meta": {
            "families": ["defect"],
            "default_params": {
                "confidence_threshold": 0.5,
                "max_defects": 1,
                "return_annotated": True,
            },
            "input_required": {"images": 1, "image_field": "image"},
            "max_images": 10,
            "note": "联调用 Mock 服务（mock_service/main.py，单进程多域共享 8002）",
        },
    },
    "dr_defect_detection": {
        "capability_id": "dr_defect_detection_v1",
        "scene": "dr",
        # 决策 31：DR 交付的是结构定位/间隙/转速分析（classification），不产标量缺陷。
        "capability_type": "classification",
        "adapter_type": "http",
        "endpoint": "http://127.0.0.1:8002",
        "health_endpoint": "http://127.0.0.1:8002/health",
        "description": "X 光透视序列，看内部结构是否到位、配合间隙是否合规（结构分析，不报缺陷个数；当前为联调模拟结果，真实模型未接入）",
        "meta": {
            "families": ["defect"],
            "default_params": {
                "confidence_threshold": 0.5,
                "max_defects": 1,
                "return_annotated": True,
            },
            "input_required": {"images": 1, "image_field": "image"},
            "max_images": 10,
            "note": "联调用 Mock 服务（mock_service/main.py，单进程多域共享 8002）",
        },
    },
    "fpi_defect_detection": {
        "capability_id": "fpi_defect_detection_v1",
        "scene": "fpi",
        "capability_type": "defect_detection",
        "adapter_type": "http",
        # 决策 47 取证（2026-09-28 实跑）：真实工具已在 18016 交付并跑通，
        # 内置表不再写 mock 8002——上一版写 8002 + mock 身份，把真工具的行洗成
        # tool_version=0.1.0/描述称 Mock 的"说谎行"。
        "endpoint": "http://127.0.0.1:18016",
        "health_endpoint": "http://127.0.0.1:18016/health",
        # 显式身份：sync 不从 mock 自报取版本（真工具与 mock 不同源）。
        # model_version 取工具 /health 自报的 `best_ckpt@sha256:…` 的短形式：
        # 完整串 81 字符，列宽 String(64) 装不下，且 64 位指纹已由 model_weights_sha256 承载。
        "identity": {
            "tool_version": "1.0.0",
            "model_version": "best_ckpt@4F2CA9AB",
            "api_version": "1.0.0",
        },
        # 实测：CPU 单次推理 2.48s、端到端 3.17s（含标注图与 PDF 产物）→ 60s 余量充足
        "timeout_s": 60,
        "max_retry": 2,
        "model_weights_sha256": "4F2CA9AB07761C54973F90844B02E6B72C1BB095F91EB25AE999E55422C631C2",
        "description": "紫外暗场里发亮的荧光显示，逐个分辨是真缺陷（BP/SP）还是伪显示（FI）",
        "meta": {
            "families": ["defect"],
            "default_params": {"part_id": "UNKNOWN", "device_id": "QC-PLATFORM"},
            "input_required": {"images": 1, "image_field": "image"},
            "max_images": 10,
            "note": (
                "真实工具 thread-defect-api-20260910（18016）；parameters 必填 part_id/device_id"
                "（extra=forbid，空对象 {} 直接 422/1001）；类别 BP/SP 计真缺陷、FI 为虚假显示；"
                "响应归一见 tool_profiles.transform_blade_defect_detector；"
                "CPU 单次推理 2.48s、端到端 3.17s（2026-09-28 实测）"
            ),
        },
    },
}

# 决策 32 护栏：sync 是**种子脚本**，不是编辑通道。
# 规则一：已存在的行只要 endpoint 与内置表声明不同，就说明这行已被人工登记或
#         对接了真实工具 —— 内置表对它没有任何事实可言，整行跳过（含描述、默认参数）。
#         上一版就是没这条：把真工具行洗成 tool_version=0.1.0 + "Mock，真实模型未到位"。
# 规则二：endpoint 匹配时也不写这三列（endpoint/health_endpoint/capability_type 是
#         人工登记事实，meta.headers 是凭证），显式 --force 才整体重置。
SYNC_PROTECTED_COLUMNS = ("endpoint", "health_endpoint", "capability_type")
MOCK_IDENTITY_COLUMNS = ("tool_version", "model_version", "api_version")


def row_is_hand_registered(existing_endpoint: str | None, spec_endpoint: str) -> bool:
    """既有行的 endpoint 与内置表声明不一致 ⇒ 这行不由内置表描述，sync 不得写它。"""
    return existing_endpoint is not None and (existing_endpoint or "") != spec_endpoint


def plan_update_fields(spec: dict, identity: dict, *, force: bool) -> dict:
    """算出非 force 路径下允许 sync 写的列（决策 32/39：只走列级 patch）。

    只在内置表确实描述该行时被调用（见 row_is_hand_registered）。
    只写内置表**显式声明过**的键：缺席即不写，否则 `spec.get(..., 默认值)` 会把
    timeout/权重指纹洗成 mock 默认值。
    """
    if force:
        return {}  # 空 = 调用方走全字段 upsert()
    fields: dict = {"scene": spec["scene"], "adapter_type": spec.get("adapter_type", "http")}
    for key in ("description", "timeout_s", "max_retry", "status", "model_weights_sha256"):
        if key in spec:
            fields[key] = spec[key]
    # 身份列：内置表给了 identity（已对接真工具）就用它，否则取 mock 自报
    row_identity = spec.get("identity") or {}
    for col in MOCK_IDENTITY_COLUMNS:
        fields[col] = row_identity.get(col, identity.get(col))
    for col in SYNC_PROTECTED_COLUMNS:
        fields.pop(col, None)
    return fields



def _load_mock_identity() -> dict:
    """在子进程加载 mock 服务并读自报身份（域名声明）。

    单进程多域的部署形态下，身份不按工具名区分；import 模块缓存无法保证
    与运行中的 mock 进程同源，故走子进程读取 --identity 输出。
    """
    mock_path = ROOT / "mock_service" / "main.py"
    proc = subprocess.run(
        [sys.executable, str(mock_path), "--identity"],
        env=os.environ,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"mock identity load failed: {proc.stderr}")
    return json.loads(proc.stdout)


async def run(force: bool = False) -> int:
    """阶段一：能力注册（原语义不变；决策 32 起对已存在行走列级护栏）。"""
    from app.repositories.capability_repo import CapabilityRepo
    from app.repositories.db import create_engine, create_session_factory
    from app.services.capability_gate import merge_meta_patch

    engine = create_engine()
    sf = create_session_factory(engine)
    repo = CapabilityRepo(sf)

    counts = {"insert": 0, "update": 0, "guarded": 0, "hand_registered": 0, "skipped": 0}
    try:
        identity = _load_mock_identity()
        for tool_name, spec in BUILTIN_CAPABILITIES.items():
            if spec["scene"] not in identity["defect_domains"]:
                counts["skipped"] += 1
                print(
                    f"[skip] {tool_name}: scene={spec['scene']} not in mock domains "
                    f"{identity['defect_domains']}"
                )
                continue
            existed = await repo.get(spec["capability_id"])
            if existed is not None and row_is_hand_registered(existed.endpoint, spec["endpoint"]):
                if not force:
                    counts["hand_registered"] += 1
                    print(f"[keep]   {spec['capability_id']}: 既有行 endpoint={existed.endpoint} "
                          f"≠ 内置表 {spec['endpoint']}，该行不由本表描述，sync 不写（--force 才重置）")
                    continue
                print(f"[force]  {spec['capability_id']}: 即将覆盖人工登记的 endpoint")
            if existed is None:
                await repo.insert(
                    capability_id=spec["capability_id"],
                    tool_name=tool_name,
                    scene=spec["scene"],
                    capability_type=spec["capability_type"],
                    adapter_type=spec["adapter_type"],
                    endpoint=spec["endpoint"],
                    health_endpoint=spec.get("health_endpoint"),
                    tool_version=(spec.get("identity") or {}).get("tool_version", identity["tool_version"]),
                    model_version=(spec.get("identity") or {}).get("model_version", identity["model_version"]),
                    api_version=(spec.get("identity") or {}).get("api_version", identity["api_version"]),
                    timeout_s=spec.get("timeout_s", 60),
                    max_retry=spec.get("max_retry", 2),
                    status=spec.get("status", "enabled"),
                    description=spec.get("description"),
                    model_weights_sha256=spec.get("model_weights_sha256"),
                    meta=spec.get("meta"),
                )
                counts["insert"] += 1
                print(f"[insert] {spec['capability_id']} -> scene={spec['scene']}, "
                      f"type={spec['capability_type']}")
                continue

            fields = plan_update_fields(spec, identity, force=force)
            if fields:
                # 护栏路径：内置表只声明"它知道的事实"，endpoint/认证头/能力类型不动
                raw_meta = json.loads(existed.meta_json or "{}")
                meta_patch = {
                    k: v for k, v in (spec.get("meta") or {}).items() if k != "headers"
                }
                fields["meta_json"] = merge_meta_patch(raw_meta, meta_patch)
                await repo.update_fields(spec["capability_id"], fields)
                protected = sorted(set(spec) - set(fields) - {"meta", "identity"})
                counts["guarded"] += 1
                print(f"[guard]  {spec['capability_id']}: 保留人工登记的 "
                      f"{protected or '（无受保护列）'}，已刷新 {sorted(fields)}")
            else:
                row_identity = spec.get("identity") or {}
                await repo.upsert(
                    capability_id=spec["capability_id"],
                    tool_name=tool_name,
                    scene=spec["scene"],
                    capability_type=spec["capability_type"],
                    adapter_type=spec["adapter_type"],
                    endpoint=spec["endpoint"],
                    health_endpoint=spec.get("health_endpoint"),
                    tool_version=row_identity.get("tool_version", identity["tool_version"]),
                    model_version=row_identity.get("model_version", identity["model_version"]),
                    api_version=row_identity.get("api_version", identity["api_version"]),
                    timeout_s=spec.get("timeout_s", 60),
                    max_retry=spec.get("max_retry", 2),
                    status=spec.get("status", "enabled"),
                    description=spec.get("description"),
                    model_weights_sha256=spec.get("model_weights_sha256"),
                    meta=spec.get("meta"),
                )
                counts["update"] += 1
                print(f"[force]  {spec['capability_id']}: 全字段重置为内置表声明值")
    finally:
        await engine.dispose()

    print(f"done: {counts}")
    return counts["insert"] + counts["update"] + counts["guarded"]


# ---------- 阶段二：data/inputs/_upload 现有图片 -> 真实质检任务（全流程） ----------

UPLOAD_DIR = ROOT / "data" / "inputs" / "_upload"
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

# 前缀 -> 场景映射（客户命名约定；hongwai=红外、kongtan=孔探，暂无对应图片，映射预留）：
#   classify_*  -> fracture  断口失效形貌分类（解理/疲劳解理/韧窝/沿晶）
#   yingguang_* -> fpi       荧光渗透检测
# classX_*（X=1~4）为试样分类图文档素材：显式忽略，绝不提交。
PREFIX_SCENE_MAP: dict[str, str] = {
    "classify": "fracture",
    "yingguang": "fpi",
}
_CLASS_X_RE = re.compile(r"^class[1-4]")

# 联调场景计划表（人工维护）：为每张图指定确定性的演示形态。
# 播种目标（每次运行必须满足）：
#   1. 状态多样 —— completed / awaiting_review / failed 三态各至少一个；
#   2. 缺陷多样 —— 不允许全部任务无缺陷；
#   3. 有缺陷必须带矩形框标注 —— mock 输出 location.bbox + 标注图
#      （manifest 默认 return_annotated=true，标注图生成依赖 Pillow）。
# 控制参数经 parameters 透传给 mock（mock_status / mock_severity / mock_error
# 为 mock 联调专用参数，见 mock_service/main.py）。
# 键 = _upload 内文件名；未登记文件按默认形态处理（不注入参数、自动确认）。
SCENARIO_PLAN: dict[str, dict] = {
    "classify_jieli_cleavage.jpg": {
        "parameters": {"mock_status": "abnormal", "mock_severity": "high"},
        "auto_confirm": False,  # 高危命中复核门后停留 -> awaiting_review 演示
        "note": "解理断裂（高危）：停留人工复核门",
    },
    "classify_pilao_cleavage.png": {
        "parameters": {"mock_status": "abnormal", "mock_severity": "high"},
        "auto_confirm": True,
        "note": "疲劳解理（高危）：自动确认后完成",
    },
    "classify_renwo_dimple.jpg": {
        "parameters": {"mock_status": "abnormal", "mock_severity": "medium"},
        "auto_confirm": True,
        "note": "韧窝（中危）：复核门自动确认后完成",
    },
    "classify_yanjing_intergranular.jpg": {
        "parameters": {"mock_status": "abnormal", "mock_severity": "low"},
        "auto_confirm": True,
        "note": "沿晶（低危）：直接完成",
    },
    "yingguang_unet_test_fdmpi.jpg": {
        "parameters": {"mock_error": True},
        "auto_confirm": True,
        "note": "模拟模型侧推理失败 -> failed（错误报告演示）",
    },
    "yingguang_yolox_test.jpg": {
        "parameters": {"mock_status": "abnormal", "mock_severity": "medium"},
        "auto_confirm": True,
        "note": "荧光缺陷（中危）：复核门自动确认后完成",
    },
    "yingguang_yolox_test2.jpg": {
        "parameters": {"mock_status": "normal"},
        "auto_confirm": True,
        "note": "无缺陷（pass）演示",
    },
}
_DEFAULT_SCENARIO: dict = {"parameters": {}, "auto_confirm": True, "note": "默认（未登记计划表）"}


def discover_pending_images(verbose: bool = False) -> list[tuple[Path, str]]:
    """扫描待分析图片：忽略 classX（X=1-4），仅保留 classify*/yingguang* 图片。

    返回 (路径, 场景) 列表，按文件名排序保证可复现。
    """
    picked: list[tuple[Path, str]] = []
    if not UPLOAD_DIR.is_dir():
        return picked
    for path in sorted(UPLOAD_DIR.iterdir()):
        if not path.is_file():
            continue
        name = path.name
        if _CLASS_X_RE.match(name):
            if verbose:
                print(f"  (忽略) {name}: classX 试样分类文档素材")
            continue
        if path.suffix.lower() not in _IMAGE_EXTS:
            continue
        for prefix, scene in PREFIX_SCENE_MAP.items():
            if name.startswith(prefix):
                picked.append((path, scene))
                break
    return picked


async def _check_mock_health() -> None:
    """提交前预检 mock 推理服务（阶段二唯一外部依赖）。"""
    url = "http://127.0.0.1:8002/health"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"mock 推理服务不可用（{url}）: {exc}\n"
            f"请先启动: python mock_service/main.py"
        ) from exc


async def _clean_existing_tasks(container) -> int:
    """清空既有任务及其全部产物（联调播种前置，保证演示数据纯净）。

    清理范围：任务行 + data/reports/{task_id} + data/artifacts/{task_id}
    + LangGraph 检查点线程（尽力而为，检查点后端不支持时跳过）。
    """
    import shutil

    from sqlalchemy import delete

    from app.repositories.task_repo import TaskRecord

    records = await container.task_repo.list_tasks(limit=100000)
    data_root = Path(container.settings.data_path)
    delete_thread = getattr(container.checkpointer, "adelete_thread", None)

    for rec in records:
        for sub in ("reports", "artifacts"):
            shutil.rmtree(data_root / sub / rec.id, ignore_errors=True)
        if delete_thread is not None:
            try:
                await delete_thread(rec.id)
            except Exception:  # noqa: BLE001 检查点清理失败不阻断播种
                pass

    if records:
        async with container.engine.begin() as conn:
            await conn.execute(delete(TaskRecord))

    return len(records)


async def run_image_tasks() -> int:
    """阶段二：清空任务表后，把 _upload 现有图片按计划表逐张提交并跑全流程。

    直连 AppContainer（与生产同一张图 + 同一套 checkpointer），不经 HTTP 层；
    计划表 auto_confirm=True 的任务停在人工复核门时自动确认续跑，
    auto_confirm=False 的任务停留 awaiting_review（人工复核门演示）。
    """
    from app.repositories.file_repo import FileRepo
    from app.repositories.task_repo import STATUS_AWAITING_REVIEW
    from app.services.container import AppContainer

    images = discover_pending_images(verbose=True)
    if not images:
        print("[phase2] data/inputs/_upload 无待分析图片（classify_*/yingguang_*），跳过提交")
        return 0

    print(f"[phase2] 发现 {len(images)} 张待分析图片:")
    for path, scene in images:
        scenario = SCENARIO_PLAN.get(path.name, _DEFAULT_SCENARIO)
        print(f"  - {path.name} -> scene={scene} ({scenario['note']})")

    # 预检通过后才清理：mock 不可用时保留既有任务，避免无谓清空
    await _check_mock_health()

    container = AppContainer()
    await container.startup()
    results = {"submitted": 0, "failed": 0}
    final_statuses: dict[str, int] = {}
    try:
        removed = await _clean_existing_tasks(container)
        print(f"[phase2] 已清空既有任务 {removed} 个（含报告/标注产物/检查点）")

        svc = container.task_service
        task_repo = container.task_repo
        assert svc is not None and task_repo is not None and container.registry is not None

        for path, scene in images:
            scenario = SCENARIO_PLAN.get(path.name, _DEFAULT_SCENARIO)
            manifest = container.registry.lookup(scene)
            if manifest is None or manifest.status != "enabled":
                results["failed"] += 1
                print(f"[fail] {path.name}: scene={scene} 能力未注册或未启用")
                continue
            uri = FileRepo.uri_for("_upload", path.name, "inputs")
            try:
                prepared = await svc.prepare(
                    scene=scene,
                    mode="single",
                    scenes=None,
                    parameters=scenario["parameters"] or None,
                    input_uris=[uri],
                    auxiliary={},
                )
                task_id = prepared["task_id"]
                await svc.run_task(task_id, prepared["state"])
                rec = await task_repo.get(task_id)
                if rec is not None and rec.status == STATUS_AWAITING_REVIEW:
                    if scenario["auto_confirm"]:
                        # HITL 复核门：联调脚本自动确认，续跑到报告生成
                        await svc.review_task(
                            task_id,
                            action="confirm",
                            opinion="脚本自动确认（真实图片联调）",
                        )
                        rec = await task_repo.get(task_id)
                    else:
                        print(f"[keep] {path.name} task={task_id}: 停留人工复核门（计划内演示）")
                grade = {}
                if rec is not None and rec.grade_json:
                    try:
                        grade = json.loads(rec.grade_json)
                    except Exception:  # noqa: BLE001
                        grade = {}
                status = rec.status if rec is not None else "unknown"
                final_statuses[status] = final_statuses.get(status, 0) + 1
                level = grade.get("level", "-") if isinstance(grade, dict) else "-"
                print(f"[done] {path.name} scene={scene} task={task_id} "
                      f"status={status} level={level}")
                results["submitted"] += 1
            except Exception as exc:  # noqa: BLE001
                results["failed"] += 1
                print(f"[fail] {path.name} scene={scene}: {exc}")
    finally:
        await container.registry.stop()
        await container.shutdown()

    print(f"[phase2] done: {results}, 状态分布={final_statuses}")
    return results["submitted"] + results["failed"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="能力注册中心同步 + data/inputs/_upload 真实图片联调提交"
    )
    parser.add_argument("--no-tasks", action="store_true",
                        help="仅注册能力，不提交图片任务")
    parser.add_argument("--dry-run", action="store_true",
                        help="注册能力 + 打印待分析图片与场景映射，不真正提交")
    parser.add_argument("--force", action="store_true",
                        help="重置已存在行的 endpoint/认证头/能力类型（决策 32 护栏的逃生门，"
                             "会洗掉 UI/DBA 的人工登记）")
    args = parser.parse_args()

    registered = asyncio.run(run(force=args.force))
    if registered < 0:
        sys.exit(1)
    if args.no_tasks:
        return
    if args.dry_run:
        print("[dry-run] 待分析图片（未提交）:")
        for path, scene in discover_pending_images(verbose=True):
            print(f"  - {path.name} -> scene={scene}")
        return
    asyncio.run(run_image_tasks())


if __name__ == "__main__":
    main()
