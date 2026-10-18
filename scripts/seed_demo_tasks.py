#!/usr/bin/env python
"""清空任务表并播种覆盖全部状态的 13 个高仿真演示任务（报告/知识/步骤/失败上下文真实感优先）。

用法（项目根目录）：
    uv run --with pillow python scripts/seed_demo_tasks.py
    目标库默认跟随 .env DATABASE_URL（平台同库），可用 --db mysql|sqlite 覆盖；

说明：
- MySQL 模式直连 quality_inspection（与平台同库，运行前清空 tasks），SQLite 模式写 data/app.db；
  平台当前实际读哪个库以 .env 为准，两者二选一时优先 MySQL；
- 图片 → data/inputs/_upload/，报告 → data/reports/{task_id}/（文件产物两模式相同）；
- 时间轴相对“当前北京时间”回退分布，全部落在今日（保证“今日”统计齐全）；
- JSON 结构逐字段对齐平台各节点产物：
  步骤节拍对齐 7 节点链（select→validate→invoke→normalize→rag→grading→human_review→generate_report）；
  判级文案逐字对齐 app/agents/nodes/grading.py，失败说明对齐 explain_error.py 模板。
- 与注册表的一致性说明：当前 capability_registry 仅 enabled 两项（borescope/fracture），
  为了演示前端全场景 UI，dr/fpi/infrared 的已完成任务按历史数据形态播种（注册表前端不可见）。

注意：MySQL 模式会 DELETE 后 INSERT 全部行（单事务），与运行中平台的写操作互斥性
由数据库自身保证；脚本执行为秒级。
"""
from __future__ import annotations

import asyncio
import json
import random
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "app.db"
UPLOAD_DIR = ROOT / "data" / "inputs" / "_upload"
REPORTS_DIR = ROOT / "data" / "reports"

UTC = timezone.utc
BJ = timezone(timedelta(hours=8))

# 图像/时间抖动用固定种子，ID 保持随机（与真实提交一致）
_RND = random.Random(20260820)

NOW = datetime.now(BJ)  # 全部时间轴锚点：当前北京时间


# ---------------------------------------------------------------- 基础工具

def new_id() -> str:
    """与 task_service.prepare 一致的 T+17 位数字串。"""
    return f"T{int(uuid4().hex[:14], 16)}"[:18]


def new_trace() -> str:
    return f"trace-{uuid4().hex[:16]}"


def _db_fmt(dt: datetime) -> str:
    """裸 UTC 墙钟（SQLite 落库形态，与 CURRENT_TIMESTAMP 一致）。"""
    return dt.astimezone(UTC).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")


def _step_iso(dt: datetime) -> str:
    """步骤时间戳（服务端序列化为带 +00:00 的 ISO 串）。"""
    return dt.astimezone(UTC).isoformat()


def _bj_iso(dt: datetime) -> str:
    """报告引用 created_at（北京时间 +08:00）。"""
    return dt.astimezone(BJ).isoformat()


def _bj_wall(dt: datetime) -> str:
    """报告 markdown 内的生成时间（北京时间墙钟，对齐 _render_markdown）。"""
    return dt.astimezone(BJ).strftime("%Y-%m-%d %H:%M:%S")


def at(minutes_ago: float) -> datetime:
    """NOW 回退 minutes_ago 分钟的北京时刻。"""
    return NOW - timedelta(minutes=minutes_ago)


def J(obj):
    return json.dumps(obj, ensure_ascii=False)


def steps_at(spec: list[tuple[float, str, str]]) -> list[dict]:
    """按绝对回退分钟构造步骤链：spec 为 (minutes_ago, node, message)，编号按时间升序。"""
    ordered = sorted(spec, key=lambda item: -item[0])
    return [{"seq": i, "node": node, "message": msg, "ts": _step_iso(at(ago))}
            for i, (ago, node, msg) in enumerate(ordered, 1)]


# ---------------------------------------------------------------- 合成图像

IMG_W, IMG_H = 800, 600


def _noise(im: Image.Image, amp: int = 8, rnd: random.Random | None = None) -> None:
    rnd = rnd or _RND
    px = im.load()
    for y in range(im.height):
        for x in range(im.width):
            p = px[x, y]
            d = rnd.randint(-amp, amp)
            if isinstance(p, int):
                px[x, y] = max(0, min(255, p + d))
            else:
                px[x, y] = tuple(max(0, min(255, c + d)) for c in p[:3])


def _crack_polyline(d: ImageDraw.ImageDraw, origin, end, steps=22, width=2):
    """从 origin 到 end 的随机蜿蜒裂纹折线（抖动随推进衰减）。"""
    (x0, y0), (x1, y1) = origin, end
    pts = []
    for i in range(steps + 1):
        t = i / steps
        x = x0 + (x1 - x0) * t
        y = y0 + (y1 - y0) * t
        y += _RND.randint(-14, 14) * (1 - t * 0.4)
        pts.append((x, y))
    d.line(pts, fill=(255, 255, 255), width=width, joint="curve")
    d.ellipse((x1 - 3, y1 - 3, x1 + 3, y1 + 3), fill=(255, 255, 255))


def _bore_base() -> Image.Image:
    """孔探圆视域 + 轮盘风扇叶片弧（内窥镜视角）。"""
    im = Image.new("RGB", (IMG_W, IMG_H), (0, 0, 0))
    d = ImageDraw.Draw(im)
    cx, cy, r = 400, 300, 288
    for i in range(6):
        a0 = i * 60 + 4
        a1 = i * 60 + 52
        g = 40 + i * 9
        d.arc((cx - r, cy - r, cx + r, cy + r), a0, a1, fill=(g, g, g + 8), width=34)
        d.arc((cx - r, cy - r, cx + r, cy + r), a0, a1 - 4, fill=(g + 26, g + 26, g + 30), width=10)
    d.ellipse((cx - 62, cy - 62, cx + 62, cy + 62), fill=(18, 18, 20))
    d.ellipse((cx - 30, cy - 30, cx + 30, cy + 30), fill=(72, 72, 76))
    for r2 in (20, 14, 8):
        d.arc((cx - r2, cy - r2, cx + r2, cy + r2), 0, 360, fill=(110, 110, 115), width=2)
    _noise(im, amp=7)
    im = im.filter(ImageFilter.GaussianBlur(0.4))
    return im


def gen_bore_clean(path: Path) -> None:
    _bore_base().save(path)


def gen_bore_crack(path: Path) -> None:
    """右上叶片根部裂纹：先模糊发光层再叠折线。"""
    im = _bore_base()
    glow = Image.new("RGB", (IMG_W, IMG_H), (0, 0, 0))
    d = ImageDraw.Draw(glow)
    _crack_polyline(d, (335, 150), (505, 295), steps=26, width=6)
    glow = glow.filter(ImageFilter.GaussianBlur(2.2))
    im = Image.blend(im, glow, 0.55)
    d2 = ImageDraw.Draw(im)
    _crack_polyline(d2, (335, 150), (505, 295), steps=26, width=2)
    im.save(path)


def gen_bore_burn(path: Path) -> None:
    """燃烧室壁面：高温灼烧变色的棕斑 + 次级局部过热亮点。"""
    im = _bore_base()
    d = ImageDraw.Draw(im)
    for xy, fill in [
        ((560, 150, 640, 228), (255, 200, 90)),
        ((508, 105, 688, 280), (150, 74, 34)),
        ((488, 88, 708, 300), (96, 44, 22)),
    ]:
        d.ellipse(xy, fill=fill)
    for xy in ((540, 130, 560, 150), (612, 162, 630, 182)):
        d.ellipse(xy, fill=(255, 244, 190))
    d.ellipse((250, 415, 316, 458), fill=(140, 70, 34))
    d.ellipse((266, 428, 288, 448), fill=(235, 205, 120))
    overlay = im.filter(ImageFilter.GaussianBlur(1.1))
    im = Image.blend(im, overlay, 0.35)
    _noise(im, amp=5)
    im.save(path)


def gen_dr_xray(path: Path) -> None:
    """数字射线底片：梯度灰场 + 叶片剪影 + 纤细亮线裂纹。"""
    im = Image.new("RGB", (IMG_W, IMG_H))
    d = ImageDraw.Draw(im)
    for y in range(IMG_H):
        g = int(150 + 60 * y / IMG_H)
        d.line((0, y, IMG_W, y), fill=(g, g, g))
    blade = [(70, 560), (180, 40), (420, 30), (690, 120), (720, 300), (660, 470), (560, 560)]
    d.polygon(blade, fill=(52, 52, 56), outline=(96, 96, 100))
    for xy in ((120, 180, 134, 194), (330, 420, 342, 432)):
        d.ellipse(xy, fill=(70, 70, 74))
    _noise(im, amp=7)
    im = im.filter(ImageFilter.GaussianBlur(0.5))
    d2 = ImageDraw.Draw(im)
    _crack_polyline(d2, (95, 130), (560, 330), steps=30, width=2)
    im.save(path)


def gen_fpi_indication(path: Path) -> None:
    """荧光渗透：近黑环境 + 叶片剪影 + 黄绿色荧光线性显示（辉光）。"""
    im = Image.new("RGB", (IMG_W, IMG_H), (6, 6, 10))
    d = ImageDraw.Draw(im)
    d.polygon([(480, 40), (760, 90), (745, 560), (470, 555)], outline=(52, 52, 58), width=2, fill=(24, 24, 28))
    tint = Image.new("RGB", (IMG_W, IMG_H), (44, 10, 74))
    mask = Image.new("L", (IMG_W, IMG_H), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse((500, 350, 900, 700), fill=70)
    md.ellipse((-140, -120, 260, 240), fill=90)
    mask = mask.filter(ImageFilter.GaussianBlur(60))
    im = Image.composite(tint, im, mask)
    glow = Image.new("RGB", (IMG_W, IMG_H), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.line([(150, 305), (232, 262), (318, 300), (392, 330), (386, 418)], fill=(185, 247, 62), width=9, joint="curve")
    glow = glow.filter(ImageFilter.GaussianBlur(3.5))
    im = Image.blend(im, glow, 0.9)
    d3 = ImageDraw.Draw(im)
    d3.line([(150, 305), (232, 262), (318, 300), (392, 330), (386, 418)], fill=(232, 255, 130), width=4, joint="curve")
    _noise(im, amp=4)
    im.save(path)


def _thermal_lut() -> list[tuple[int, int, int]]:
    """黑→紫→红→橙→黄 5 段插值 LUT（256 级）。"""
    stops = [(0, (5, 5, 18)), (63, (58, 8, 96)), (127, (186, 24, 58)), (191, (240, 122, 20)), (255, (255, 235, 120))]
    lut: list[tuple[int, int, int]] = []
    for i in range(256):
        for (v0, c0), (v1, c1) in zip(stops, stops[1:]):
            if v0 <= i <= v1:
                t = (i - v0) / (v1 - v0)
                lut.append(tuple(round(c0[k] + (c1[k] - c0[k]) * t) for k in range(3)))
                break
    return lut


def gen_ir_hotspot(path: Path) -> None:
    """红外热像：金属圆盘等温场 + 叶缘高温区（涂层脱落热占位）。"""
    gray = Image.new("L", (IMG_W, IMG_H), 40)
    d = ImageDraw.Draw(gray)
    d.ellipse((150, 40, 650, 560), fill=72)
    d.ellipse((230, 120, 570, 480), fill=60)
    d.ellipse((330, 220, 470, 380), fill=52)
    hot = Image.new("L", (IMG_W, IMG_H), 0)
    hd = ImageDraw.Draw(hot)
    hd.ellipse((515, 110, 668, 240), fill=235)
    hd.ellipse((556, 142, 598, 184), fill=255)
    hot = hot.filter(ImageFilter.GaussianBlur(26))
    hot_arr = hot.load()
    ga = gray.load()
    for y in range(IMG_H):
        for x in range(IMG_W):
            ga[x, y] = min(255, ga[x, y] + hot_arr[x, y])
    _noise(gray, amp=6)
    lut = _thermal_lut()
    rgb = Image.new("RGB", (IMG_W, IMG_H))
    rgb.putdata([lut[gray.getpixel((x, y))] for y in range(IMG_H) for x in range(IMG_W)])
    rgb.save(path)


def _sem_base(striations: bool) -> Image.Image:
    """SEM 断口：底噪颗粒 + 解理平台 + 可选疲劳辉纹弧带/放射河流花样。"""
    im = Image.effect_noise((IMG_W, IMG_H), 42).convert("RGB")
    d = ImageDraw.Draw(im)
    for _ in range(900):
        x, y = _RND.randint(0, IMG_W - 1), _RND.randint(0, IMG_H - 1)
        r = _RND.randint(1, 3)
        c = 96 + _RND.randint(0, 130)
        d.ellipse((x - r, y - r, x + r, y + r), outline=(c, c, c))
    for _ in range(14):
        x, y = _RND.randint(60, IMG_W - 90), _RND.randint(60, IMG_H - 90)
        w, h = _RND.randint(40, 130), _RND.randint(26, 70)
        d.polygon([(x, y), (x + w, y + 6), (x + w - 14, y + h), (x + 8, y + h - 6)], fill=(30, 30, 34))
    if striations:
        d.ellipse((104, 128, 128, 152), fill=(170, 170, 174))
        for r in range(34, 300, 14):
            d.arc((128 - r, 140 - r, 128 + r, 140 + r), -150, 60,
                  fill=(120 + (r % 30), 120 + (r % 30), 124 + (r % 30)), width=2)
        for _ in range(30):
            x, y = _RND.randint(150, 640), _RND.randint(180, 460)
            d.line((x, y, x + 60, y + 140), fill=(74, 74, 78), width=2)
    im = im.filter(ImageFilter.GaussianBlur(0.9))
    _noise(im, amp=5)
    return im


def gen_sem_dimple(path: Path) -> None:
    _sem_base(striations=False).save(path)


def gen_sem_fatigue(path: Path) -> None:
    _sem_base(striations=True).save(path)


def gen_radiance_csv(path: Path) -> None:
    lines = [
        "frame_id,t_ms,mean_gray,std_gray,emissivity,reflect_temp_c,target_temp_c",
        "0,0,118.4,7.2,0.95,24.0,61.3",
        "1,200,117.9,7.5,0.95,24.0,61.0",
        "2,400,121.6,8.1,0.95,24.0,63.8",
        "3,600,139.2,14.6,0.95,24.0,78.5",
        "4,800,167.8,22.3,0.95,24.0,96.2",
        "5,1000,188.5,26.9,0.95,24.0,112.4",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def gen_all_images() -> dict[str, str]:
    files = {
        "bore_blade_clean.png": gen_bore_clean,
        "bore_blade_crack.png": gen_bore_crack,
        "bore_combustor_burn.png": gen_bore_burn,
        "dr_blade_xray.png": gen_dr_xray,
        "fpi_blade_indication.png": gen_fpi_indication,
        "ir_turbine_hotspot.png": gen_ir_hotspot,
        "sem_fracture_dimple.png": gen_sem_dimple,
        "sem_fracture_fatigue.png": gen_sem_fatigue,
    }
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    for name, fn in files.items():
        fn(UPLOAD_DIR / name)
        print(f"  + 图像 {name}")
    gen_radiance_csv(UPLOAD_DIR / "ir_radiance_20260820.csv")
    print("  + 图像 ir_radiance_20260820.csv")
    return {k: f"/api/v1/tasks/_upload/files/{k}?kind=inputs" for k in files}


# ---------------------------------------------------------------- 知识库目录

def _k(chunk_id: str, doc_name: str, score: float, content: str) -> dict:
    return {"chunk_id": chunk_id, "doc_name": doc_name, "score": score, "content": content, "image_uri": None}


KB = {
    "borescope": [
        _k("3a7c9f2d8b1e4a0c85c26d3a7e9b41d2", "航空发动机涡轮叶片孔探检测规程.pdf", 0.81,
           "孔探检测应重点检查叶身进气边、排气边及叶根转接 R 区。裂纹类缺陷在齿形定向反射下呈连续亮线，"
           "伴随机位变化其形态跟随叶片表面移动；灼伤区表现为表面氧化变色与局部反光率下降。"),
        _k("c8e14b6d2a7f4c90a3e1d5b7c2f6a8e3", "TC4 钛合金叶片缺陷分析案例集.md", 0.74,
           "TC4 叶片服役缺陷以进排气边疲劳裂纹与叶尖磨损为主，疲劳裂纹多起源于叶根 R 区机加刀痕，"
           "扩展方向垂直于主应力轴。当裂纹深度超过壁厚 30% 时应判为不合格。"),
        _k("9d2f7b3c8a5e4601b7c9d2e4f0a6b8c", "航发部件表面缺陷检测案例汇编.md", 0.63,
           "表面灼烧缺陷通常伴随局部组织过热，叶片前缘灼烧在孔探镜下呈棕褐色异色斑，需与燃油积碳沉积相区分，"
           "积碳多呈黑色疏松附着且可用无纺布擦除。"),
    ],
    "fracture": [
        _k("b4d8a1c6e2f4903b7c5e9d0a2f6b1c7", "航空发动机构件断口分析图谱.pdf", 0.88,
           "疲劳断口由疲劳源区、扩展区与瞬断区组成。疲劳源区可见放射状河流花样汇聚；扩展区可见疲劳辉纹，"
           "瞬断区呈韧窝形貌。源区数量与位置直接决定构件断裂的起始原因。"),
        _k("e7f2c9a5d3b8e401a6c0d9f2b7e4a1d", "GH738 镍基高温合金疲劳断裂研究.pdf", 0.79,
           "GH738 涡轮盘榫槽疲劳裂纹的扩展速率在 650℃ 下显著升高，断口高倍观察可见沿晶与韧窝混合形貌，"
           "沿晶断裂比例升高通常提示晶界弱化（碳化物连续析出或环境侵蚀）。"),
    ],
    "dr": [
        _k("f1a9c3b7e5d2f4908a6c1e0b3d8f5a2", "涡轮叶片数字射线检测工艺规程.pdf", 0.83,
           "数字射线检测底片上，裂纹呈纤细的亮线或锯齿状线状显示，与气孔（圆形亮斑）和夹渣（不规则暗斑）相区分。"
           "线性显示的评定应测量长度并确认是否贯穿壁厚，判定按放大倍率 1:1 实测。"),
        _k("a6d3f8b1c9e4a2705b8d2e6f0c3a9d1", "射线检测底片评定方法手册.md", 0.71,
           "底片评定应控制几何不清晰度，叶片类中空结构建议双胶片双角度透照。疑似线性缺陷需换角度复透，"
           "确认非伪影后方可记录，并按缺陷等级表出具不合格结论。"),
    ],
    "fpi": [
        _k("c2b7e9a4d1f6a4830b5c8e2d9a0f3b6", "航空发动机渗透检测(FPI)工艺规程.pdf", 0.85,
           "后乳化型荧光渗透检测的显示在紫外灯 365nm 下呈黄绿色荧光。线性显示（长宽比＞3:1）按裂纹对待，"
           "圆形显示按气孔/缩孔对待；显示尺寸评定应在显像后 30 分钟内完成。"),
        _k("7e1d4a9c6b2f8e305a7c9d1e3b5f0a8", "荧光渗透显示图谱与判定.md", 0.69,
           "真实裂纹显示呈连续、锐利的荧光线条，擦拭后重复显像每次位置一致；伪显示（缸体印痕、荧光污染）"
           "擦拭后不再显现或呈弥散状态。线性显示超过验收判据即判定零件不通过。"),
    ],
    "infrared": [
        _k("d9c4e2a7f0b3d4851c6a9f2e8b4d0c9", "红外热像检测方法与应用.pdf", 0.78,
           "稳态热像法对构件施加热激励后采集表面温度场，涂层脱落区域因热阻增大呈现局部高温热斑或低温冷点，"
           "其位置与激励源几何相关，需结合反射温度补偿修正发射率差异。"),
        _k("5b8a2f6d1c9e4a7302b7d9c3e1f6a8b", "热像检测叶片涂层脱粘判定案例.md", 0.66,
           "热斑温差超过背景 15% 且连续 5 帧不衰减时，判定为可疑脱粘区域；单一帧峰值不做判定依据。"
           "可疑区应复核发射率设置并更换激励方向复测一次。"),
    ],
}


# ---------------------------------------------------------------- 判级/复核构件

def defect(did, dtype, cn, conf, sev, bbox, desc) -> dict:
    return {"defect_id": did, "defect_type": dtype, "defect_name_cn": cn,
            "confidence": conf, "severity": sev, "location": {"type": "bbox", "bbox": bbox},
            "description": desc}


def grade_of(level: str, summary: str, rules: list[str], max_sev, min_conf, family="defect",
             auto=True, confirmed=None) -> dict:
    g = {"level": level, "summary": summary, "triggered_rules": rules,
         "max_severity": max_sev, "min_confidence": min_conf, "family": family, "auto": auto}
    if confirmed is not None:
        g["confirmed"] = confirmed
    return g


def review_interrupt(task_id: str, scene: str, grade: dict, standardized: list, knowledge: list) -> dict:
    """human_review 中断载荷（review_json 在 awaiting_review 状态下即为此形态）。"""
    return {"question": "检测结果存在高风险或低置信度项，请人工复核",
            "task_id": task_id, "scene": scene, "grade": grade,
            "standardized": standardized, "knowledge": knowledge,
            "decision_options": ["confirm", "revise", "requeue", "reject"]}


def inputs(uris: list[str], aux: dict | None = None) -> str:
    return J({"scenes": [], "input_uris": uris, "auxiliary": aux or {}, "parameters": {}})


def raw(tool: str, defects: list[dict] | None = None) -> str:
    return J({"tool": {"tool_name": tool, "tool_version": "v0.7.2", "model_version": "det-2026q2", "api_version": "v1"},
              "result": {"defects": defects if defects is not None else []}})


def agent_report_ref(task_id: str, created_dt: datetime) -> dict:
    return {"report_type": "agent_report", "name": f"{task_id}_report.md",
            "uri": f"/api/v1/tasks/{task_id}/files/report.md?kind=reports",
            "version": "1.0.0", "created_at": _bj_iso(created_dt)}


def error_report_ref(task_id: str, created_dt: datetime) -> dict:
    return {"report_type": "error_report", "name": f"{task_id}_error_report.md",
            "uri": f"/api/v1/tasks/{task_id}/files/error_report.md?kind=reports",
            "version": "1.0.0", "created_at": _bj_iso(created_dt)}


# ---------------------------------------------------------------- 报告渲染

def render_agent_report(t: dict) -> str:
    """严格按 generate_report._render_markdown 骨架渲染。"""
    g = json.loads(t["grade_json"]) or {}
    entries = json.loads(t["standardized_json"]) if t.get("standardized_json") else []
    knowledge = json.loads(t["knowledge_json"]) if t.get("knowledge_json") else []
    review = json.loads(t["review_json"]) if t.get("review_json") else {"action": "auto_confirm",
                                                                         "opinion": "规则自动通过，无需人工复核"}
    sc = t.get("scene") or "-"
    cap = t.get("capability_used") or "-"
    hint = t.get("grade_hint") or {}
    lines = [
        f"# 智能质检报告 {t['id']}",
        "",
        f"- 生成时间：{t['report_time']}",
        f"- 检测场景：{sc}",
        f"- 算法工具：{cap}（{t.get('tool_version', 'v0.7.2')}/{t.get('model_version', 'det-2026q2')}）",
        f"- 判级结论：**{g['level']}**  {g['summary']}",
        "",
        "## 1. 结果概览",
        "",
        f"- 状态：{hint.get('status', '-')} | 最高严重度：{hint.get('max_severity', '-')} | 最低置信度：{hint.get('min_confidence', '-')}",
        "",
    ]
    if entries:
        lines.append("## 2. 标准化检测结果")
        lines.append("")
        lines.append("| 项目 | 数值 |")
        lines.append("|---|---|")
        for e in entries:
            lines.append(f"| 缺陷 {e.get('defect_type')} | 置信度 {e.get('confidence')} / 严重度 {e.get('severity')} |")
    else:
        lines.append("（无标准化明细）")
    lines.append("")
    if knowledge:
        lines.append("## 3. 知识库引用")
        lines.append("")
        for idx, k in enumerate(knowledge, 1):
            lines.append(f"{idx}. [{k.get('doc_name')}](score={k.get('score')}) {k.get('content', '')[:120]}")
    else:
        lines.append("## 3. 知识库引用")
        lines.append("")
        lines.append("（未配置 RAGFlow 或未检索到相关条目）")
    lines.append("")
    lines.append("## 4. 人工复核记录")
    lines.append("")
    lines.append(f"- 复核动作：{review.get('action', 'auto_confirm')}")
    if review.get("opinion"):
        lines.append(f"- 复核意见：{review['opinion']}")
    if review.get("reviewer"):
        lines.append(f"- 复核人：{review['reviewer']}")
    lines.append("")
    section_no = 5
    if t.get("metric_note"):
        lines.append(f"## {section_no}. 指标解释")
        lines.append("")
        lines.append(t["metric_note"].strip())
        lines.append("")
        section_no += 1
    if t.get("llm_lead"):
        lines.append(f"## {section_no}. AI 综合分析")
        lines.append("")
        lines.append(t["llm_lead"].strip())
        lines.append("")
    lines.append("---")
    lines.append("> 本报告由智能质检 Agent 自动生成，最终判定责任由业务专家确认。")
    return "\n".join(lines)


def render_error_report(t: dict) -> str:
    """严格按 explain_error._render_error_markdown 骨架渲染。"""
    error = json.loads(t["error_json"]) or {}
    return "\n".join([
        f"# 智能质检任务失败说明 {t['id']}",
        "",
        f"- 生成时间：{t['report_time']}",
        f"- 失败节点：{error.get('node', '-')}",
        "",
        "## 1. 失败概要",
        "",
        error["summary"],
        "",
        "## 2. 失败上下文",
        "",
        "```json",
        json.dumps({"error": error, "scene": t.get("scene"),
                    "mode": t.get("mode"), "trace_id": t.get("trace_id")},
                   ensure_ascii=False, default=str, indent=2),
        "```",
        "",
        "---",
        "> 该任务未通过质检流程，请按上述原因处理后重试或转人工处理。",
    ])


# ---------------------------------------------------------------- 行构造

COLS = ["id", "trace_id", "scene", "mode", "status", "capability_used",
        "inputs_json", "raw_results_json", "standardized_json", "knowledge_json",
        "grade_json", "review_json", "report_json", "steps_json", "error_json",
        "created_at", "updated_at", "finished_at", "node_index"]


def row(d: dict) -> tuple:
    return tuple(d.get(c) for c in COLS)


def base(task_id: str, scene, mode, status, cap, created_ago, inputs_json,
         standardized=None, knowledge=None, grade=None, review=None,
         report=None, steps=None, error=None, raw_results=None) -> dict:
    d = {
        "id": task_id, "trace_id": new_trace(), "scene": scene, "mode": mode,
        "status": status, "capability_used": cap, "inputs_json": inputs_json,
        "raw_results_json": raw_results, "standardized_json": J(standardized) if standardized else None,
        "knowledge_json": J(knowledge) if knowledge else None,
        "grade_json": J(grade) if grade else None, "review_json": J(review) if review else None,
        "report_json": J(report) if report else None,
        "steps_json": J(steps) if steps else J([]),
        "error_json": J(error) if error else None,
        "created_at": _db_fmt(at(created_ago)),
    }
    if steps:
        updated = datetime.fromisoformat(steps[-1]["ts"])
        d["updated_at"] = _db_fmt(updated)
        if status in ("completed", "failed"):
            d["finished_at"] = _db_fmt(updated)
    else:
        d["updated_at"] = d["created_at"]
    d["node_index"] = len(steps or [])
    return d


# 标准 7 节点节拍（分钟回退，含各节点耗时感：选择→校验→推理→标准化→RAG 多轮→判级→报告）
def auto_beats(c_ago: float, rag_gap: float = 2.9, report_gap: float = 2.3):
    return [
        (c_ago - 0.7, "select_capability", "ok"),
        (c_ago - 0.8, "validate", "ok"),
        (c_ago - 1.3, "invoke_capability", "ok"),
        (c_ago - 1.36, "normalize_result", "ok"),
        (c_ago - 1.36 - rag_gap, "rag_retrieval", "ok"),
        (c_ago - 1.76 - rag_gap, "grading", "ok"),
        (c_ago - 1.76 - rag_gap - report_gap, "generate_report", "ok"),
    ]


def reviewed_beats(c_ago: float, decide_gap: float = 21.0):
    """review/fail 判级 → human_review 中断 → 人工确认后 resume 报告。"""
    base_beats = auto_beats(c_ago)[:-1]
    hr = base_beats[-1][0] - 0.1
    return base_beats + [(hr, "human_review", "ok"), (hr - decide_gap, "generate_report", "ok")]


def awaiting_beats(c_ago: float):
    return auto_beats(c_ago)[:-1] + [(auto_beats(c_ago)[-2][0] - 0.1, "human_review", "ok")]


TASKS: list[dict] = []


def build_tasks(uris: dict[str, str]) -> list[dict]:
    U = uris

    def done(t: dict) -> dict:
        TASKS.append(t)
        return t

    # ---- 1. pending：孔探任务排队中（已上传，等待调度） ----
    t1_id = new_id()
    done(base(t1_id, "borescope", "single", "pending", None, 6.5,
              inputs([U["bore_blade_crack.png"]])))

    # ---- 2. running：joint 联合模式（混合输入单候选自动采用，推理中） ----
    t2_id = new_id()
    done(base(t2_id, None, "joint", "running", None, 6.0,
              inputs([U["ir_turbine_hotspot.png"], U["dr_blade_xray.png"]]),
              steps=steps_at([(5.3, "select_capability", "ok"), (5.0, "validate", "ok")])))

    # ---- 3. awaiting_review：孔探高温灼烧（触发 max_severity >= 3） ----
    t3_id = new_id()
    t3_std = [
        defect("D001", "burn", "烧伤", 0.61, "high", [475, 145, 645, 250],
               "燃烧室壁面高温灼烧斑（棕红氧化变色），中心区亮度异常"),
        defect("D002", "corrosion", "腐蚀", 0.63, "low", [215, 390, 300, 455],
               "次级氧化腐蚀斑块，边界不规则"),
    ]
    t3_grade = grade_of("review", "规则命中需要人工复核：max_severity >= 3",
                        ["max_severity >= 3"], "high", 0.61)
    done(base(t3_id, "borescope", "single", "awaiting_review", "borescope_defect_detection", 133.0,
              inputs([U["bore_combustor_burn.png"]]),
              standardized=t3_std, knowledge=KB["borescope"][:2], grade=t3_grade,
              review=review_interrupt(t3_id, "borescope", t3_grade, t3_std, KB["borescope"][:2]),
              steps=steps_at(awaiting_beats(133.0)),
              raw_results=raw("borescope_defect_detection",
                              [{k: v for k, v in d.items() if k != "description"} for d in t3_std])))

    # ---- 4. awaiting_review：断口疲劳（同时触发 max_severity>=3 与 min_confidence<=0.5） ----
    t4_id = new_id()
    t4_std = [
        defect("D001", "fatigue_crack", "疲劳裂纹", 0.81, "high", [60, 40, 340, 240],
               "疲劳源区放射状河流花样汇聚，源点位于试样角部"),
        defect("D002", "intergranular", "沿晶断裂", 0.38, "low", [430, 340, 640, 470],
               "晶界弱化区域沿晶开裂小平面，置信度低于采纳阈值"),
    ]
    t4_grade = grade_of("review", "规则命中需要人工复核：max_severity >= 3、min_confidence <= 0.5",
                        ["max_severity >= 3", "min_confidence <= 0.5"], "high", 0.38)
    done(base(t4_id, "fracture", "single", "awaiting_review", "fracture_defect_detection", 158.0,
              inputs([U["sem_fracture_fatigue.png"]]),
              standardized=t4_std, knowledge=KB["fracture"], grade=t4_grade,
              review=review_interrupt(t4_id, "fracture", t4_grade, t4_std, KB["fracture"]),
              steps=steps_at(awaiting_beats(158.0)),
              raw_results=raw("fracture_defect_detection",
                              [{k: v for k, v in d.items() if k != "description"} for d in t4_std])))

    # ---- 5. completed：孔探洁净叶片 PASS（自动判级） ----
    t5_id = new_id()
    t5_grade = grade_of("pass", "未发现缺陷，无需人工复核", [], None, None)
    t5_steps = steps_at(auto_beats(62.0))
    t5 = base(t5_id, "borescope", "single", "completed", "borescope_defect_detection", 62.0,
              inputs([U["bore_blade_clean.png"]]),
              standardized=[], knowledge=KB["borescope"][:1], grade=t5_grade,
              review={"action": "auto_confirm", "opinion": "规则自动通过，无需人工复核"},
              report=agent_report_ref(t5_id, datetime.fromisoformat(t5_steps[-1]["ts"])),
              steps=t5_steps, raw_results=raw("borescope_defect_detection"))
    t5["report_time"] = _bj_wall(datetime.fromisoformat(t5_steps[-1]["ts"]))
    t5["grade_hint"] = {"status": "normal", "max_severity": None, "min_confidence": None}
    t5["llm_lead"] = """### 1. 任务概述

本任务对某型先进涡扇发动机高压涡轮工作叶片实施孔探检测，图像为内窥镜在燃烧室出口视角采集，覆盖叶身前缘至叶根转接区。检测目标为裂纹、烧伤、腐蚀三类表面缺陷，判级规则采用「严重度 + 置信度」双重阈值，任务编号 %s，trace_id %s。

### 2. 检测结果分析

算法 borescope_defect_detection（v0.7.2 / det-2026q2）对输入图像完成 7 个方向的定向反射扫描，未检出高于 0.5 置信度阈值的缺陷区域，标准化结果为空，判级线索 status=normal。叶片表面反射纹路均匀，未见异常亮线或异色斑块。

### 3. 知识库佐证

依据《航空发动机涡轮叶片孔探检测规程.pdf》：裂纹在定向反射下呈连续亮线，灼伤区表现为氧化变色与局部反光率下降。本图像两项特征均未出现，与干净叶片的孔探形态一致。

### 4. 风险评估

最高严重度 null、最低置信度 null，未触发任何人工复核规则。残留风险主要来自孔探视角遮挡（叶盆侧约 40%% 区域不可见）与反射伪影误判，建议结合后续齿形测量与涡流检测交叉验证。

### 5. 合规性说明

本次检测依据内部工艺规程 Q/HT-NDT-021 执行，图像采集分辨率满足 ≥5 像素/mm 要求，判级过程全程留痕（7 个节点，trace_id 可回溯）。

### 6. 结论与建议

判级结论 **pass**：未发现缺陷，无需人工复核。建议将该叶片纳入正常流转批次，下次检修周期按常规间隔开展孔探复检。""" % (t5_id, t5["trace_id"])
    done(t5)

    # ---- 6. completed：孔探叶根裂纹（defect 低严重度，自动判级） ----
    t6_id = new_id()
    t6_std = [defect("D001", "crack", "裂纹", 0.72, "low", [320, 140, 520, 310],
                     "叶根 R 区疑似裂纹亮线，沿机加刀痕方向延伸约 12.4mm")]
    t6_grade = grade_of("defect", "存在低严重度缺陷，建议回修", [], "low", 0.72)
    t6_steps = steps_at(auto_beats(205.0))
    t6 = base(t6_id, "borescope", "single", "completed", "borescope_defect_detection", 205.0,
              inputs([U["bore_blade_crack.png"]]),
              standardized=t6_std, knowledge=KB["borescope"][:2], grade=t6_grade,
              review={"action": "auto_confirm", "opinion": "规则自动通过，无需人工复核"},
              report=agent_report_ref(t6_id, datetime.fromisoformat(t6_steps[-1]["ts"])),
              steps=t6_steps,
              raw_results=raw("borescope_defect_detection",
                              [{k: v for k, v in d.items() if k != "description"} for d in t6_std]))
    t6["report_time"] = _bj_wall(datetime.fromisoformat(t6_steps[-1]["ts"]))
    t6["grade_hint"] = {"status": "detected", "max_severity": "low", "min_confidence": 0.72}
    t6["llm_lead"] = """### 1. 任务概述

本任务对涡轮叶片叶根转接 R 区开展孔探缺陷检测，工件为 TC4 钛合金精锻叶片。任务编号 %s。

### 2. 检测结果分析

检出缺陷 D001 裂纹（置信度 0.72，严重度 low），位置 bbox [320, 140, 520, 310]，对应叶根 R 区机加刀痕带，亮线沿轴向延伸约 12.4mm，形态连续、随视场移动跟随叶片表面。该区域为疲劳裂纹典型萌生位置，但当前裂纹深度评估未超过壁厚 30%%。

### 3. 知识库佐证

《TC4 钛合金叶片缺陷分析案例集.md》指出：TC4 叶片疲劳裂纹多起源于叶根 R 区机加刀痕，扩展方向垂直于主应力轴；《航空发动机涡轮叶片孔探检测规程.pdf》给出裂纹的定向反射亮线判读依据，与本检出的连续亮线形态吻合。

### 4. 风险评估

最高严重度 low、最低置信度 0.72（高于 0.5 采纳阈值），未触发人工复核规则。但叶根 R 区为高应力集中位置，裂纹存在扩展风险，建议转入回修工序并加强后续监控。

### 5. 合规性说明

检测与判级共 7 个节点全程留痕，标准化结果与判级结论均落库可审计，符合工艺规程对叶根区域的必检项要求。

### 6. 结论与建议

判级结论 **defect**：存在低严重度缺陷，建议回修。建议对缺陷位置进行局部修磨并复检确认裂纹完全去除，复检时增加侧向观察角度以排除反光伪影。""" % t6_id
    done(t6)

    # ---- 7. completed：断口疲劳裂纹（fail 判级 → 人工确认不合格） ----
    t7_id = new_id()
    t7_std = [
        defect("D001", "fatigue_crack", "疲劳裂纹", 0.81, "medium", [85, 60, 300, 180],
               "裂纹源区放射状河流花样，源点位于试样角部"),
        defect("D002", "dimple", "韧窝", 0.68, "low", [420, 330, 640, 440],
               "扩展区等轴韧窝聚集区，局部可见沿晶小平面"),
    ]
    t7_grade = grade_of("fail", "存在中/高严重度缺陷，判为不合格", [], "medium", 0.68,
                        auto=False, confirmed=True)
    t7_review = {"action": "confirm",
                 "opinion": "疲劳源区形貌清晰，放射脊与辉纹特征典型，同意判级不合格并转失效分析。",
                 "grading_result": "confirmed"}
    t7_steps = steps_at(reviewed_beats(249.0))
    t7 = base(t7_id, "fracture", "single", "completed", "fracture_defect_detection", 249.0,
              inputs([U["sem_fracture_dimple.png"]]),
              standardized=t7_std, knowledge=KB["fracture"], grade=t7_grade, review=t7_review,
              report=agent_report_ref(t7_id, datetime.fromisoformat(t7_steps[-1]["ts"])),
              steps=t7_steps,
              raw_results=raw("fracture_defect_detection",
                              [{k: v for k, v in d.items() if k != "description"} for d in t7_std]))
    t7["report_time"] = _bj_wall(datetime.fromisoformat(t7_steps[-1]["ts"]))
    t7["grade_hint"] = {"status": "detected", "max_severity": "medium", "min_confidence": 0.68}
    t7["metric_note"] = """- **检出率（Fracture）**：疲劳裂纹影像级检出率 92.3%，优于去年同期 88.7%；
- **框定精度**：IoU 均值 0.847，较模板级基线提高 6.1 个百分点（评价集 430 张断口影像）；
- **评分一致性**：判级结果与三名金相工程师独立评级 Kendall W 系数 0.91。"""
    t7["llm_lead"] = """### 1. 任务概述

本任务为压气机盘榫槽断裂件断口扫描分析，断口在扫描电镜下 200×~2000× 多视场采集。任务编号 %s。

### 2. 检测结果分析

检出两处缺陷：D001 疲劳裂纹（置信度 0.81，medium）位于源区 bbox [85, 60, 300, 180]，可见放射状河流花样汇聚；D002 韧窝（0.68，low）位于扩展区等轴韧窝聚集带。疲劳源点位于试样角部，提示应力集中源与装配接触相关。

### 3. 知识库佐证

《航空发动机构件断口分析图谱.pdf》指出疲劳断口三区特征（源区/扩展区/瞬断区）与本标本吻合；《GH738 镍基高温合金疲劳断裂研究.pdf》提示榫槽疲劳裂纹沿晶断裂比例升高与晶界碳化物连续析出相关，建议对断口做能谱验证。

### 4. 风险评估

最高严重度 medium、最低置信度 0.68。判级为 **fail**（不合格）后进入人工复核，复核人确认疲劳源区形貌典型。残余风险：次生裂纹深度未定量，建议补充断口定量分析。

### 5. 合规性说明

判级结论经人工确认（复核动作 confirm），复核记录与判级依据一并落库，满足失效分析任务的双重把关要求。

### 6. 结论与建议

判级结论 **fail**：存在中/高严重度缺陷，判为不合格。建议将该断口移交失效分析专业组开展断口定量与裂纹扩展速率评估，并在同批次榫槽部位安排扩检。""" % t7_id
    done(t7)

    # ---- 8. completed：DR 射线裂纹（fail 判级 → 人工确认不合格） ----
    t8_id = new_id()
    t8_std = [defect("D001", "crack", "裂纹", 0.78, "medium", [80, 105, 585, 345],
                     "叶片排气边纵向线性显示，长约 18.6mm，纤细亮线贯穿壁厚")]
    t8_grade = grade_of("fail", "存在中/高严重度缺陷，判为不合格", [], "medium", 0.78,
                        auto=False, confirmed=True)
    t8_review = {"action": "confirm",
                 "opinion": "底片线性显示连续且换角度复透位置一致，确认裂纹贯穿壁厚，同意不合格。",
                 "grading_result": "confirmed"}
    t8_steps = steps_at(reviewed_beats(227.0, decide_gap=22.5))
    t8 = base(t8_id, "dr", "single", "completed", "dr_defect_detection", 227.0,
              inputs([U["dr_blade_xray.png"]]),
              standardized=t8_std, knowledge=KB["dr"], grade=t8_grade, review=t8_review,
              report=agent_report_ref(t8_id, datetime.fromisoformat(t8_steps[-1]["ts"])),
              steps=t8_steps,
              raw_results=raw("dr_defect_detection",
                              [{k: v for k, v in d.items() if k != "description"} for d in t8_std]))
    t8["report_time"] = _bj_wall(datetime.fromisoformat(t8_steps[-1]["ts"]))
    t8["grade_hint"] = {"status": "detected", "max_severity": "medium", "min_confidence": 0.78}
    t8["llm_lead"] = """### 1. 任务概述

本任务对高压涡轮导向叶片实施数字射线检测（DR），采用双胶片双角度透照，图像为排气边侧底片。任务编号 %s。

### 2. 检测结果分析

检出 D001 裂纹（置信度 0.78，medium），bbox [80, 105, 585, 345]，为排气边纵向线性显示，长约 18.6mm，与气孔类圆形亮斑形态明显可区分。换角度复透后位置一致，排除伪影。

### 3. 知识库佐证

《涡轮叶片数字射线检测工艺规程.pdf》给出线状显示判定方法：与气孔/夹渣形态区分、按 1:1 实测长度并确认是否贯穿壁厚；《射线检测底片评定方法手册.md》要求疑似线性缺陷换方向复透，本任务均已执行。

### 4. 风险评估

最高严重度 medium，判级 **fail**。裂纹位于排气边高应力区且贯穿壁厚，服役中扩展风险高。人工复核确认显示连续、无伪影。

### 5. 合规性说明

透照参数与底片评定全程留痕，判级经人工确认（confirm）。底片图像以 DICONDE 数字底片格式归档。

### 6. 结论与建议

判级结论 **fail**：存在中/高严重度缺陷，判为不合格。建议该叶片判废处理，同批次叶片增加排气边双角度透照抽检比例。""" % t8_id
    done(t8)

    # ---- 9. completed：FPI 荧光线性显示（defect 低严重度自动） ----
    t9_id = new_id()
    t9_std = [defect("D001", "crack", "裂纹", 0.66, "low", [130, 285, 410, 435],
                     "荧光线性显示，长宽比>3:1，疑似表面开口裂纹")]
    t9_grade = grade_of("defect", "存在低严重度缺陷，建议回修", [], "low", 0.66)
    t9_steps = steps_at(auto_beats(268.0))
    t9 = base(t9_id, "fpi", "single", "completed", "fpi_defect_detection", 268.0,
              inputs([U["fpi_blade_indication.png"]]),
              standardized=t9_std, knowledge=KB["fpi"], grade=t9_grade,
              review={"action": "auto_confirm", "opinion": "规则自动通过，无需人工复核"},
              report=agent_report_ref(t9_id, datetime.fromisoformat(t9_steps[-1]["ts"])),
              steps=t9_steps,
              raw_results=raw("fpi_defect_detection",
                              [{k: v for k, v in d.items() if k != "description"} for d in t9_std]))
    t9["report_time"] = _bj_wall(datetime.fromisoformat(t9_steps[-1]["ts"]))
    t9["grade_hint"] = {"status": "detected", "max_severity": "low", "min_confidence": 0.66}
    t9["llm_lead"] = """### 1. 任务概述

本任务对涡轮叶片实施后乳化型荧光渗透检测（FPI），输入为紫外灯 365nm 下采集的显像照片。任务编号 %s。

### 2. 检测结果分析

检出 D001 裂纹（置信度 0.66，low），bbox [130, 285, 410, 435]，为沿叶片转接带分布的黄绿色荧光线性显示，长宽比大于 3:1，按规程按裂纹对待。显示连续锐利，擦拭后重复显像位置一致，排除荧光污染伪显示。

### 3. 知识库佐证

《航空发动机渗透检测(FPI)工艺规程.pdf》明确线性显示按裂纹对待并要求 30 分钟内完成评定；《荧光渗透显示图谱与判定.md》给出真伪显示区分方法，本次显示符合真实裂纹特征。

### 4. 风险评估

最高严重度 low、最低置信度 0.66。显示位于转接带表面，深度未定量（渗透法无法测深），建议回修后复检确认。

### 5. 合规性说明

渗透检测工艺参数（渗透时间/显像时间/紫外照度）符合规程要求，检测与判级记录全程留痕。

### 6. 结论与建议

判级结论 **defect**：存在低严重度缺陷，建议回修。建议局部修磨后复做 FPI 复检，如显示复现则转 DR 定量测深。""" % t9_id
    done(t9)

    # ---- 10. completed：红外热斑（defect 低严重度自动，含辐射值 CSV） ----
    t10_id = new_id()
    t10_std = [defect("D001", "burn", "烧伤", 0.79, "low", [520, 110, 670, 240],
                      "叶尖区域热斑，温差较背景高 21.3℃，疑似涂层脱落型热占位")]
    t10_grade = grade_of("defect", "存在低严重度缺陷，建议回修", [], "low", 0.79)
    t10_steps = steps_at(auto_beats(299.0))
    t10 = base(t10_id, "infrared", "single", "completed", "infrared_defect_detection", 299.0,
               inputs([U["ir_turbine_hotspot.png"]],
                      aux={"辐射值CSV": "/api/v1/tasks/_upload/files/ir_radiance_20260820.csv?kind=inputs"}),
               standardized=t10_std, knowledge=KB["infrared"], grade=t10_grade,
               review={"action": "auto_confirm", "opinion": "规则自动通过，无需人工复核"},
               report=agent_report_ref(t10_id, datetime.fromisoformat(t10_steps[-1]["ts"])),
               steps=t10_steps,
               raw_results=raw("infrared_defect_detection",
                               [{k: v for k, v in d.items() if k != "description"} for d in t10_std]))
    t10["report_time"] = _bj_wall(datetime.fromisoformat(t10_steps[-1]["ts"]))
    t10["grade_hint"] = {"status": "detected", "max_severity": "low", "min_confidence": 0.79}
    t10["metric_note"] = """- **温场重建误差**：黑体标定板下 0.42℃（全量程 0~450℃），满足规程 ≤1℃ 要求；
- **热斑判定一致性**：连续 5 帧温差衰减率判据与人工复核一致率 94.7%；
- **辐射值 CSV 匹配**：输入 CSV 5 帧采集中 4 帧通过峰值对齐校验，1 帧抖动在容差内。"""
    t10["llm_lead"] = """### 1. 任务概述

本任务对涡轮盘表面涂层实施红外热像检测，输入为稳态热激励下的温度场图像及配套辐射值 CSV（5 帧）。任务编号 %s。

### 2. 检测结果分析

检出 D001 烧伤（置信度 0.79，low），bbox [520, 110, 670, 240]，位于叶尖区域，热斑温差较背景高 21.3℃，连续 5 帧未衰减，符合可疑脱粘区域的判定准则。

### 3. 知识库佐证

《红外热像检测方法与应用.pdf》指出涂层脱落区域因热阻增大呈局部高温热斑，位置与激励源几何相关；《热像检测叶片涂层脱粘判定案例.md》的「温差超背景 15%% 且连续 5 帧不衰减」判据与本次结果一致。

### 4. 风险评估

最高严重度 low、最低置信度 0.79。热斑位置靠近叶尖高气流冲刷区，如确认为涂层脱粘则存在基材氧化风险，建议安排目视或 FPI 对位复核。

### 5. 合规性说明

热像仪黑体标定与反射温度补偿记录完整，辐射值 CSV 与图像峰值对齐校验通过。

### 6. 结论与建议

判级结论 **defect**：存在低严重度缺陷，建议回修。建议脱粘可疑区更换激励方向复测一次，并安排孔探对位目视确认涂层状态。""" % t10_id
    done(t10)

    # ---- 11. failed：3002 适配器不可达（复审后用户重试，重试中推理服务失联） ----
    t11_id = new_id()
    t11_steps = steps_at([
        (351.3, "select_capability", "ok"),
        (351.2, "validate", "ok"),
        (350.7, "invoke_capability", "ok"),
        (350.64, "normalize_result", "ok"),
        (347.8, "rag_retrieval", "ok"),
        (347.4, "grading", "ok"),
        (347.28, "human_review", "ok"),
        (224.0, "retry", "task retried by user"),
        (223.8, "select_capability", "ok"),
        (223.7, "validate", "ok"),
        (222.2, "invoke_capability", "ok"),
    ])
    t11 = base(t11_id, "borescope", "single", "failed", None, 352.0,
               inputs([U["bore_blade_crack.png"]]),
               error={"node": "invoke_capability", "code": 3002,
                      "message": "adapter error: tool borescope_defect_detection unreachable: All connection attempts failed",
                      "summary": "任务在算法能力调用阶段失败：检测工具 borescope_defect_detection 所在推理服务当前不可达，"
                                 "重试的多次连接尝试均被拒绝。该问题与图像内容无关，属于推理服务网络或部署故障；"
                                 "建议确认推理服务在线状态与注册地址后重试。之前的首轮检测曾完成判级并触发人工复核，"
                                 "复核记录可供追溯。"},
               report=error_report_ref(t11_id, datetime.fromisoformat(t11_steps[-1]["ts"])),
               steps=t11_steps)
    t11["report_time"] = _bj_wall(datetime.fromisoformat(t11_steps[-1]["ts"]))
    done(t11)

    # ---- 12. failed：3003 推理超时（断口大图推理 120s 未返回） ----
    t12_id = new_id()
    t12_steps = steps_at([
        (255.1, "select_capability", "ok"),
        (255.0, "validate", "ok"),
        (254.1, "invoke_capability", "ok"),
    ])
    t12 = base(t12_id, "fracture", "single", "failed", None, 259.0,
               inputs([U["sem_fracture_fatigue.png"]]),
               error={"node": "invoke_capability", "code": 3003,
                      "message": "adapter error: tool fracture_defect_detection timeout after 120.0s",
                      "summary": "算法推理未在 120s 限定窗口内返回结果，判定为推理超时。断裂面扫描图分块开销较大，"
                                 "且同一时刻并发推理任务较多，可能触发资源排队。建议调低输入分辨率、减少分块数量，"
                                 "或联系平台管理员扩容推理资源后重试。"},
               report=error_report_ref(t12_id, datetime.fromisoformat(t12_steps[-1]["ts"])),
               steps=t12_steps)
    t12["report_time"] = _bj_wall(datetime.fromisoformat(t12_steps[-1]["ts"]))
    done(t12)

    # ---- 13. failed：4004 场景无可用能力（金相场景路由未命中注册表） ----
    t13_id = new_id()
    t13_steps = steps_at([(383.7, "select_capability", "ok")])
    t13 = base(t13_id, "metallography", "single", "failed", None, 384.0,
               inputs(["/api/v1/tasks/_upload/files/class4_1.jpg?kind=inputs"]),
               error={"node": "select_capability", "code": 4004,
                      "message": "场景 metallography 无可用能力",
                      "summary": "任务提交后场景路由阶段未能在能力注册表中找到与「metallography（金相组织）」"
                                 "匹配的检测能力，场景路由失败。当前平台已注册能力仅覆盖 孔探、断口 等场景。"
                                 "请确认能力清单，或联系平台管理员注册对应算法后方可重新提交。"},
               report=error_report_ref(t13_id, datetime.fromisoformat(t13_steps[-1]["ts"])),
               steps=t13_steps)
    t13["report_time"] = _bj_wall(datetime.fromisoformat(t13_steps[-1]["ts"]))
    done(t13)

    return TASKS


def write_reports(tasks: list[dict]) -> None:
    for t in tasks:
        if not t["report_json"]:
            continue
        ref = json.loads(t["report_json"])
        folder = REPORTS_DIR / t["id"]
        folder.mkdir(parents=True, exist_ok=True)
        if ref["report_type"] == "error_report":
            (folder / "error_report.md").write_text(render_error_report(t), encoding="utf-8")
        else:
            (folder / "report.md").write_text(render_agent_report(t), encoding="utf-8")
        print(f"  + 报告 {t['id']} ({ref['report_type']})")


def _mysql_cfg() -> dict | None:
    """从 .env 解析 MySQL 连接信息；非 MySQL 配置返回 None。"""
    env = ROOT / ".env"
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("DATABASE_URL"):
            continue
        url = line.split("=", 1)[1].strip().strip('"').strip("'")
        parsed = urlparse(url)
        if parsed.scheme not in ("mysql", "mysql+aiomysql"):
            return None
        return {
            "host": parsed.hostname or "localhost",
            "port": parsed.port or 3306,
            "user": unquote(parsed.username or "root"),
            "password": unquote(parsed.password or ""),
            "db": parsed.path.lstrip("/"),
        }
    return None


async def _persist_mysql(cfg: dict, rows: list[tuple]) -> dict[str, int]:
    """MySQL 写入：清空 tasks 后整表 INSERT（单事务），返回分组统计。"""
    import aiomysql  # 平台运行时依赖，兼容无该依赖的环境（SQLite 模式不触发导入）

    sql = f"INSERT INTO tasks ({', '.join(COLS)}) VALUES ({', '.join(['%s'] * len(COLS))})"
    con = await aiomysql.connect(
        host=cfg["host"], port=cfg["port"], user=cfg["user"],
        password=cfg["password"], db=cfg["db"], autocommit=False,
    )
    try:
        async with con.cursor() as cur:
            await cur.execute("DELETE FROM tasks")
            await cur.executemany(sql, rows)
        await con.commit()
        async with con.cursor() as cur:
            await cur.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status")
            stats = {s: n for s, n in await cur.fetchall()}
        return stats
    finally:
        con.close()


def _persist_sqlite(rows: list[tuple]) -> dict[str, int]:
    sql = f"INSERT INTO tasks ({', '.join(COLS)}) VALUES ({', '.join(['?'] * len(COLS))})"
    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        con.execute("DELETE FROM tasks")
        con.executemany(sql, rows)
        con.commit()
        stats = dict(con.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status").fetchall())
        return stats
    finally:
        con.close()


def _target_backend() -> str:
    """--db mysql|sqlite 显式覆盖，否则跟随 .env DATABASE_URL。"""
    if "--db" in sys.argv:
        want = sys.argv[sys.argv.index("--db") + 1].lower()
        if want in ("mysql", "sqlite"):
            return want
        raise SystemExit(f"未知 --db 值: {want}（可选 mysql / sqlite）")
    return "mysql" if _mysql_cfg() else "sqlite"


def main() -> None:
    backend = _target_backend()
    print(f"目标库：{'MySQL (' + _mysql_cfg()['db'] + ')' if backend == 'mysql' else 'SQLite (data/app.db)'}")
    print("1/4 清理旧数据 …")
    if REPORTS_DIR.exists():
        shutil.rmtree(REPORTS_DIR)
        print("  - 已清空 data/reports/")
    for stale in ("borescope_demo.png", "fracture_demo.png"):
        p = UPLOAD_DIR / stale
        if p.exists():
            p.unlink()
            print(f"  - 移除占位图 {stale}")

    print("2/4 生成合成图像 …")
    uris = gen_all_images()

    print("3/4 写入任务表 …")
    tasks = build_tasks(uris)
    rows = [row(t) for t in tasks]
    if backend == "mysql":
        stats = asyncio.run(_persist_mysql(_mysql_cfg(), rows))
    else:
        stats = _persist_sqlite(rows)

    print("4/4 写入报告文件 …")
    write_reports(tasks)

    # 汇总
    print()
    by: dict[str, list] = {}
    for t in tasks:
        by.setdefault(t["status"], []).append(t)
    for status in ("pending", "running", "awaiting_review", "completed", "failed"):
        for t in by.get(status, []):
            print(f"  [{t['status']:<16}] {t['id']}  scene={(t['scene'] or '-'):<14} mode={t['mode']}")
    print()
    print("统计:", ", ".join(f"{k}={v}" for k, v in sorted(stats.items())), f"| 合计={sum(stats.values())}")
    print()
    print("播种完成。平台进程无需重启（与平台读写同一库）。")


if __name__ == "__main__":
    main()