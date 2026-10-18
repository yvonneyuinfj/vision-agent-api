"""真实工具 4 场景 E2E：孔探18410 / 热端18015 / X光18011 / 金相18013。

对每场景用 AppContainer（与平台同一张图 + 同一 checkpointer，不经 HTTP/auth）
跑一个真实质检任务：prepare → run_task 全流程（智能体选中并调用真实工具 →
normalize 归一 → 判级 → 复核门 → 报告生成），并在复核门停住时自动确认续跑。

验证项（对每任务）：
  1. status == completed（复核门自动确认到终态）
  2. capability_used == 该场景真实能力（证明智能体正确选中真实工具）
  3. standardized_json（归一缺陷列表）非空/空符合语义——孔探/热端→检出缺陷，
     X光/金相→分析类 normal（不伪造成标量缺陷）
  4. grade_json 判级：孔探/热端=defect|review（有缺陷）；X光/金相=pass（分析放行）
  5. report_json 非空（报告已产出）

用法：
    uv run python -m scripts.dev.run_real_tools_e2e
"""
from __future__ import annotations

import asyncio
import json

from app.repositories.file_repo import FileRepo
from app.repositories.task_repo import STATUS_AWAITING_REVIEW
from app.services.container import AppContainer

# 输入文件（已用 e2e_* 平铺文件名 stage 到 data/inputs/_upload/，见脚本顶注释）
SCENARIOS = [
    {
        "scene": "borescope",
        "capability": "borescope_defect_detection_v1",
        "tool": "borescope_defect_detection",
        "name": "孔探 18014",
        "inputs": ["e2e_borescope.jpg"],
        "expect": "defect",   # 孔探 → 缺陷
        "need_real_abnormal": True,
    },
    {
        "scene": "infrared",
        "capability": "infrared_defect_detection_v1",
        "tool": "infrared_defect_detection",
        "name": "热端 18015",
        "inputs": ["e2e_hotsection_abnormal.png"],
        "expect": "defect",   # 热端 → 缺陷
        "need_real_abnormal": True,
    },
    {
        "scene": "dr",
        "capability": "dr_defect_detection_v1",
        "tool": "dr_defect_detection",
        "name": "X光/DR 18011",
        "inputs": [
            "e2e_dr_state041.opi",
            "e2e_dr_state042.opi",
        ],
        "expect": "analysis",  # X光 → 分析上报
        "need_real_abnormal": False,
    },
    {
        "scene": "metallography",
        "capability": "metallography_defect_detection_v1",
        "tool": "metallography_defect_detection",
        "name": "金相断口 18013",
        "inputs": ["e2e_metallography.png"],
        "expect": "analysis",  # 金相 → 分析上报
        "need_real_abnormal": False,
    },
]


def _uri(name: str) -> str:
    # 复用 sync_capabilities 的 _upload 命名空间：文件须平铺在 data/inputs/_upload/
    return FileRepo.uri_for("_upload", name, "inputs")


async def run_one(container: AppContainer, spec: dict) -> dict:
    svc = container.task_service
    task_repo = container.task_repo
    manifest = container.registry.lookup(spec["scene"])
    if manifest is None or manifest.status != "enabled":
        return {"scene": spec["scene"], "ok": False, "reason": "能力未注册/未启用"}

    input_uris = [_uri(n) for n in spec["inputs"]]
    print(f"\n===== {spec['name']} :: scene={spec['scene']} =====")
    print(f"  capability = {manifest.capability_id} -> {manifest.endpoint}")
    print(f"  inputs = {input_uris}")

    prepared = await svc.prepare(
        scene=spec["scene"],
        mode="single",
        scenes=None,
        parameters=None,
        input_uris=input_uris,
        auxiliary={},
    )
    task_id = prepared["task_id"]
    await svc.run_task(task_id, prepared["state"])
    rec = await task_repo.get(task_id)

    if rec is not None and rec.status == STATUS_AWAITING_REVIEW:
        # HITL 复核门：脚本自动确认，随后异步恢复跑报告——轮询到终态
        await svc.review_task(
            task_id, action="confirm", opinion="E2E 脚本自动确认（真实工具联调）"
        )
        for _ in range(120):  # 最长 ~4min 等待报告生成
            await asyncio.sleep(2)
            rec = await task_repo.get(task_id)
            if rec is not None and rec.status in ("completed", "failed"):
                break
        else:
            rec = await task_repo.get(task_id)
    else:
        rec = await task_repo.get(task_id)

    result = {
        "scene": spec["scene"],
        "task_id": task_id,
        "status": rec.status if rec else "missing",
        "capability_used": rec.capability_used if rec else None,
        "standardized": rec.standardized_json if rec else None,
        "report": rec.report_json if rec else None,
        "grade": rec.grade_json if rec else None,
        "steps": rec.steps_json if rec else None,
        "ok": False,
        "checks": {},
    }

    checks = result["checks"]
    checks["status_completed"] = result["status"] == "completed"
    # capability_used 存的是 tool_name（如 borescope_defect_detection）
    checks["capability_correct"] = result["capability_used"] in (spec.get("tool"), spec["capability"])

    # 工具确实被调用的证据：任务完成 + 报告产出。standardized_json 为归一缺陷列表
    # （JSON 数组）；grade_json 为判级结论；X光产物含大量分析图即真实调用佐证。
    try:
        std_list = json.loads(result["standardized"] or "[]")
        checks["normalized"] = isinstance(std_list, list)
        result["defect_list"] = std_list
        result["defect_count"] = len(std_list) if isinstance(std_list, list) else None
    except Exception:
        checks["normalized"] = False

    # 判级结论（权威语义证据）：level + summary
    grade_summary = ""
    grade_level = ""
    try:
        g = json.loads(result["grade"] or "{}") if result["grade"] else {}
        grade_level = str(g.get("level") or "")
        grade_summary = str(g.get("summary") or "")
    except Exception:
        pass
    result["grade_level"] = grade_level
    result["grade_summary"] = grade_summary

    # 报告引用存在 = 报告已产出
    report_ok = bool(result["report"])
    checks["report_generated"] = report_ok
    if report_ok:
        try:
            rep = json.loads(result["report"])
            result["report_type"] = rep.get("report_type")
            result["report_name"] = rep.get("name")
        except Exception:
            pass

    # 真实调用+归一待判定：存在标准化缺陷 或 判级在 detect/analysis 域见分晓
    checks["tool_result_reflected"] = result.get("defect_count", 0) > 0

    # 语义归一校验（以判级结论为权威证据，与任务语义映射一致）
    if spec["expect"] == "defect" and spec["need_real_abnormal"]:
        # 孔探/热端 → 应检出缺陷并判级（defect/review）
        checks["real_defect"] = result.get("defect_count", 0) > 0
        checks["semantic_defect"] = grade_level in ("defect", "review")
    elif spec["expect"] == "analysis":
        # X光/金相 → 分析类（不伪造成标量缺陷；判级 pass + 定量/结构分析）
        checks["semantic_analysis"] = (
            result.get("defect_count") == 0 and grade_level in ("pass",) and bool(grade_summary)
        )

    result["ok"] = all(
        v for k, v in checks.items()
        if k not in ("real_defect", "tool_result_reflected")
    ) and (checks.get("real_defect", True))

    # 打印
    print(f"  task={task_id} status={result['status']}")
    print(f"  capability_used={result['capability_used']}")
    print(f"  standardized defects={result.get('defect_count')}")
    if result.get("report_type"):
        print(f"  report_type={result['report_type']} name={result.get('report_name')}")
    if result.get("grade_level"):
        print(f"  grade level={result['grade_level']} summary={result['grade_summary'][:120]}")
    print(f"  checks={json.dumps(checks, ensure_ascii=False)}")
    print(f"  RESULT ok={result['ok']}")

    return result


async def main() -> None:
    container = AppContainer()
    await container.startup()
    try:
        results = []
        for spec in SCENARIOS:
            try:
                results.append(await run_one(container, spec))
            except Exception as exc:  # noqa: BLE001
                print(f"[EXC] {spec['scene']}: {exc!r}")
                results.append({"scene": spec["scene"], "ok": False, "reason": repr(exc)})
        print("\n\n========== E2E 汇总 ==========")
        all_ok = True
        for r in results:
            ok = r.get("ok", False)
            all_ok = all_ok and ok
            print(
                f"  [{('PASS' if ok else 'FAIL')}] {r.get('scene')} "
                f"task={r.get('task_id')} status={r.get('status')}"
            )
            if not ok:
                print(f"        reason/checks: {r.get('reason') or r.get('checks')}")
        print(f"\nALL PASS: {all_ok}")
    finally:
        await container.registry.stop()
        await container.shutdown()


if __name__ == "__main__":
    asyncio.run(main())