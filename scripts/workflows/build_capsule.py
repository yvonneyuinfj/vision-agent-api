#!/usr/bin/env python3
"""构建 qc-inspection workflow capsule。

权威源：scripts/workflows/qc-inspection.source.js（人类可读可评审）
产物：  .dsh/workflows/qc-inspection.workflow.json（@dsh-external/workflow 插件消费，
        项目级发现路径，git 共享后团队成员各自实例可按名运行）

用法：  python3 scripts/workflows/build_capsule.py
"""
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "scripts/workflows/qc-inspection.source.js"
OUT_DIR = ROOT / ".dsh/workflows"
OUT = OUT_DIR / "qc-inspection.workflow.json"

SCENES = ["borescope", "infrared", "dr", "fpi", "fracture", "metallography"]

capsule = {
    "format": "dsh.workflow",
    "version": 1,
    "workflowApiVersion": 1,
    "minDshVersion": "0.0.1-rc.2",
    "manifest": {
        "name": "qc-inspection",
        "description": (
            "Harness 原生质检平台流程（不依赖 :8000 FastAPI 平台）：意图解析→工具巡检→"
            "直调统一 API 推理(:8002 专业工具)→源码确定性规则判级(移植 grade_rules.yaml)→"
            "HITL 复核门→本地知识增强→综合研判→报告归档。任务留痕落 data/workflow-tasks/，"
            "报告与标注图落 data/workflow-reports/；支持 single/batch/joint 三模式，"
            "run 证据由插件持久化。"
        ),
        "phases": ["intake", "dispatch", "inspect", "grade", "review", "knowledge", "synthesize", "report"],
        "readOnly": False,
        "plannedAgents": 6,
        "maxAgents": 24,
        "maxConcurrency": 3,
        "mayUseWorktree": False,
        "patterns": ["classify-and-act", "fan-out-and-synthesize"],
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "requirement": {"type": "string"},
                "images": {"type": "array", "items": {"type": "string"}},
                "scenes": {"type": "array", "items": {"type": "string", "enum": SCENES}},
                "mode": {"type": "string", "enum": ["single", "batch", "joint"]},
                "mockStatus": {"type": "string", "enum": ["normal", "abnormal"]},
                "autoReview": {"type": "boolean"},
            },
        },
    },
    "source": SOURCE.read_text(encoding="utf-8"),
    "intent": {
        "taskClass": "harness-native-quality-inspection-platform",
        "patterns": ["classify-and-act", "fan-out-and-synthesize"],
        "reusableFor": [
            "单图单场景检测全流程（harness 原生平台形态）",
            "批量图像 pipeline 检测",
            "多场景联合检测与综合研判摘要",
            "一期验收场景的端到端演练",
        ],
        "notFor": [
            "生产环境多用户并发服务（多用户 RBAC/内网交付仍需独立服务平台）",
            "模型训练/评估流程",
        ],
    },
    "inputs": {
        "description": (
            "运行参数三级合并：显式 args > scripts/workflows/run-config.json > 内置默认。"
            "requirement=自然语言检测需求；images=图像路径数组（相对项目根）；"
            "scenes=可选显式场景；mode=single|batch|joint；"
            "mockStatus=mock 结果倾向（abnormal 触发人工复核门）；"
            "autoReview=true 时复核门无人值守自动放行。"
            "不带 args 运行时执行内置默认（单图 borescope 演示链路）。"
        ),
        "examples": [
            {
                "requirement": "对这批涡轮叶片孔探图像进行缺陷检测并生成检测报告",
                "images": ["docs/image/class1_1.jpg"],
                "scenes": ["borescope"],
                "mode": "single",
                "mockStatus": "abnormal",
                "autoReview": False,
            },
            {
                "requirement": "批量筛查全部样例图像的表面缺陷",
                "images": ["docs/image/class1_1.jpg", "docs/image/class2_1.jpg"],
                "mode": "batch",
                "autoReview": True,
            },
        ],
    },
    "provenance": {
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dshVersion": "0.0.1-rc.2",
        "pluginVersion": "0.1.2",
    },
}

OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(capsule, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"built: {OUT} ({OUT.stat().st_size} bytes)")
