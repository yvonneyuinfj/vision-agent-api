# -*- coding: utf-8 -*-
"""在《航空发动机智能质检系统_招标技术参数表-0901.xlsx》中补充本项目设计点条款。

新增条款（与项目实际实现对齐，便于投标响应逐条对应）：
- 会话管理（质检助手对话轨）：会话创建/重命名/删除、消息流、SSE 实时推送、
  多模态带图消息、场景确认卡片、任务落定事件回注、对话内就地复核。
- 文件防重上传：SHA-256 内容寻址、秒传复用、引用计数、本地落盘兜底。
- 统一响应外壳与链路追踪：code/message/task_id/trace_id 外壳、X-Trace-Id 全链路贯通。
- 检查点持久化与断点恢复：LangGraph Checkpointer（PG/SQLite）、人工复核 interrupt 恢复。
- 前端工程化与可访问性：React18+TS+Vite+AntD、深色/浅色主题、键盘焦点、减少动效偏好。

两处插入：
1. 招标技术参数表：在「第三章 多智能体编排与任务流程」末尾（第47条之后，即第30行）插入会话管理 3 条；
   在「第七章 安全、权限、审计与运维」末尾（第63条之后，即第83行）插入文件防重/链路追踪/断点恢复 3 条；
   在「第八章 性能、可靠性与兼容性」末尾（第66-1条之后，即第89行）插入前端工程化 1 条。
   插入顺序从后往前，避免行号偏移。
2. 投标响应表：同样位置镜像插入相同条款（响应结论等列留空），并保持填写说明公式引用区间
   同步扩大（A3:A86 -> A3:A90 等）。

注意：openpyxl 的 insert_rows 不会自动调整合并单元格/公式/冻结窗格，脚本内显式处理。
"""
from __future__ import annotations

import re
import shutil
from copy import copy
from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.cell_range import CellRange

SRC = Path("docs/原始材料/航空发动机智能质检系统_招标技术参数表-0901.xlsx")
DST = SRC  # 原地更新（调用方已确认）；如需另存请改这里
BAK = SRC.with_suffix(".xlsx.bak")

# ---------------------------------------------------------------------------
# 新条款定义：(序号, 参数项, 技术要求, 属性, 验收标准/证明要求)
# 插入位置用「锚点序号」描述：插到该序号所在行之后。
# ---------------------------------------------------------------------------
NEW_ROWS_SESSION = [
    (
        "45-1",
        "会话管理 / 质检助手对话轨",
        "应提供面向质检工程师的对话式工作台，支持会话创建、重命名、删除与最近列表；"
        "会话内支持文本与检测图像混合消息，助手回复、思考过程、任务卡片、复核卡片、"
        "完成卡片与报告卡片应以独立消息类型区分并持久化留痕，刷新后可完整回放。",
        "▲",
        "演示会话全生命周期操作；抽查消息记录表，核对多类型卡片消息持久化与历史回放一致性。",
    ),
    (
        "45-2",
        "会话管理 / 实时推送与断线恢复",
        "会话消息流应采用服务端推送（SSE 或等效机制）实现助手逐字回复、工具调用过程、"
        "任务状态变化的近实时呈现；首帧应全量回放历史，断线重连后不得丢消息或重复渲染；"
        "应提供心跳保活与轮询兜底，弱网环境下体验不中断。",
        "一般",
        "演示弱网/刷新/断线重连场景下消息不丢不重；核对推送协议帧格式与心跳机制。",
    ),
    (
        "45-3",
        "会话管理 / 任务联动与就地复核",
        "对话中派发的检测任务应与会话绑定，任务状态变化（待复核、完成、失败）应主动回注会话；"
        "进入人工复核时应在对话内弹出复核卡片，支持确认、修订、退回补检、驳回四种动作就地提交，"
        "无需跳转页面；服务重启后应对账补发滞留的复核卡片，不得遗漏。",
        "▲",
        "演示对话内从派发到复核、报告就绪的全流程；模拟服务重启，验证复核卡补发与幂等（不重复弹卡）。",
    ),
]

NEW_ROWS_SECURITY = [
    (
        "61-1",
        "文件治理 / 防重上传与内容寻址",
        "检测图像与附件上传应采用内容寻址（SHA-256）去重：同一文件重复上传只存储一份并复用已有引用（秒传），"
        "引用计数随任务关联递增；对象存储不可用时须可降级为本地受控目录落盘，主流程不阻断；"
        "文件读取应经白名单与路径穿越防护，禁止任意路径访问。",
        "▲",
        "同一文件重复上传验证秒传与存储占用不增长；断对象存储演练本地降级；构造畸形路径验证拦截。",
    ),
    (
        "51-1",
        "追溯 / 统一响应外壳与链路标识",
        "全部接口应返回统一响应外壳（业务码、消息、任务号、链路标识等），"
        "每请求生成或透传链路标识（trace_id）并贯穿任务接收、模型调用、知识检索、判级、复核、报告归档各环节，"
        "支持按标识一次定位完整调用链；非 JSON 异常响应应统一转为结构化外壳，不得泄露堆栈。",
        "▲",
        "抽取任一任务按链路标识还原全链路记录；构造 404/500 等异常验证统一外壳与无堆栈泄露。",
    ),
    (
        "63-1",
        "可靠性 / 流程断点持久化与恢复",
        "编排流程状态（含人工复核中断点）应持久化到数据库，服务重启、Worker 漂移后任务可断点续跑；"
        "人工复核挂起的任务不得因重启丢失；多实例部署时检查点应支持并发安全存取。",
        "▲",
        "任务挂起复核状态下重启服务，验证恢复后可继续复核与归档；核对检查点存储与并发取断点机制。",
    ),
]

NEW_ROWS_COMPAT = [
    (
        "66-2",
        "前端工程化 / 可访问性与主题",
        "前端应采用主流工程化栈构建并支持深色/浅色主题切换；交互组件须覆盖键盘焦点可见、"
        "非颜色唯一提示、减少动态效果偏好等可访问性要求；界面、报告与运维文档使用中文，"
        "专业英文缩写首次出现给出中文解释。",
        "一般",
        "主流浏览器抽测主题切换与键盘路径；核对 prefers-reduced-motion 生效与中文文案规范。",
    ),
]


def shift_merged_ranges(ws, start_row: int, count: int) -> None:
    """把 start_row 及之后的合并区域整体下移 count 行。"""
    new_ranges: list[str] = []
    for mr in list(ws.merged_cells.ranges):
        cr = CellRange(str(mr))
        if cr.min_row >= start_row:
            cr.shift(0, 0)  # no-op 保持类型
            cr = CellRange(
                min_col=cr.min_col,
                min_row=cr.min_row + count,
                max_col=cr.max_col,
                max_row=cr.max_row + count,
            )
        new_ranges.append(str(cr))
    ws.merged_cells.ranges = []
    for r in new_ranges:
        ws.merged_cells.add(r)


def copy_row_style(ws, src_row: int, dst_row: int, max_col: int) -> None:
    for col in range(1, max_col + 1):
        s = ws.cell(row=src_row, column=col)
        d = ws.cell(row=dst_row, column=col)
        if s.has_style:
            d.font = copy(s.font)
            d.fill = copy(s.fill)
            d.border = copy(s.border)
            d.alignment = copy(s.alignment)
            d.number_format = s.number_format
            d.protection = copy(s.protection)


def insert_rows_with_style(ws, anchor_row: int, new_rows: list[tuple], *, max_col: int) -> None:
    """在 anchor_row 之后插入 new_rows；样式复制自 anchor_row 的数据行。"""
    if not new_rows:
        return
    count = len(new_rows)
    insert_at = anchor_row + 1  # 新行起始位置
    ws.insert_rows(insert_at, amount=count)
    shift_merged_ranges(ws, insert_at, count)
    for i, row_data in enumerate(new_rows):
        r = insert_at + i
        copy_row_style(ws, anchor_row, r, max_col)
        for c_idx, val in enumerate(row_data, start=1):
            ws.cell(row=r, column=c_idx, value=val)


def find_row_by_first_col(ws, needle: str) -> int:
    for row in ws.iter_rows(min_col=1, max_col=1):
        v = row[0].value
        if v is not None and str(v).strip() == needle:
            return row[0].row
    raise ValueError(f"anchor row not found: {needle} in {ws.title}")


def bump_formulas(ws, mapping: list[tuple[str, str]]) -> None:
    for row in ws.iter_rows():
        for cell in row:
            v = cell.value
            if isinstance(v, str) and v.startswith("="):
                new_v = v
                for old, new in mapping:
                    new_v = new_v.replace(old, new)
                if new_v != v:
                    cell.value = new_v


def main() -> None:
    if not BAK.exists():
        shutil.copy2(SRC, BAK)
        print(f"backup -> {BAK}")

    wb = openpyxl.load_workbook(SRC)

    # ---------- 招标技术参数表 ----------
    ws = wb["招标技术参数表"]
    # 从后往前插，避免锚点行号失效
    r_661 = find_row_by_first_col(ws, "66-1")      # 第八章末条
    r_63 = find_row_by_first_col(ws, "63")         # 第七章末条
    r_47 = find_row_by_first_col(ws, "47")         # 第三章末条
    print(f"anchors: 47@{r_47}, 63@{r_63}, 66-1@{r_661}")

    insert_rows_with_style(ws, r_661, NEW_ROWS_COMPAT, max_col=5)
    insert_rows_with_style(ws, r_63, NEW_ROWS_SECURITY, max_col=5)
    insert_rows_with_style(ws, r_47, NEW_ROWS_SESSION, max_col=5)

    # 更新第二章标题行注释（第2行：共 84 条 -> 共 91 条）
    head = ws.cell(row=2, column=1).value
    if isinstance(head, str):
        head_new = head.replace("共 84 条", "共 91 条")
        # 属性统计：原 ★14 ▲25 一般45；新增 ▲4（45-1/45-3/61-1/51-1/63-1 共5个▲? 重新数）
        # 新增：45-1▲ 45-2一般 45-3▲ 61-1▲ 51-1▲ 63-1▲ 66-2一般 → ▲+5 一般+2 = 7条
        head_new = re.sub(r"▲\s*25\s*条", "▲ 30 条", head_new)
        ws.cell(row=2, column=1, value=head_new)
        print("header:", head_new)

    # ---------- 投标响应表（镜像，属性列同步；实现说明等留空） ----------
    ws2 = wb["投标响应表"]
    r2_57_1 = find_row_by_first_col(ws2, "57-1")
    # 第八章 66-1 之后插 66-2；第七章 63 之后插三条；第三章 47 之后插三条
    r2_661 = find_row_by_first_col(ws2, "66-1")
    r2_63 = find_row_by_first_col(ws2, "63")
    r2_47 = find_row_by_first_col(ws2, "47")

    def mirror(rows):
        return [(a, b, c, d, "", "", "", "") for (a, b, c, d, _e) in rows]

    insert_rows_with_style(ws2, r2_661, mirror(NEW_ROWS_COMPAT), max_col=8)
    insert_rows_with_style(ws2, r2_63, mirror(NEW_ROWS_SECURITY), max_col=8)
    insert_rows_with_style(ws2, r2_47, mirror(NEW_ROWS_SESSION), max_col=8)

    # ---------- 填写说明：公式区间扩大（86 -> 93）----------
    ws3 = wb["填写说明"]
    bump_formulas(
        ws3,
        [
            ("投标响应表!A3:A86", "投标响应表!A3:A93"),
            ("投标响应表!$D$3:$D$86", "投标响应表!$D$3:$D$93"),
            ("投标响应表!E3:E86", "投标响应表!E3:E93"),
            ("投标响应表!$E$3:$E$86", "投标响应表!$E$3:$E$93"),
        ],
    )

    wb.save(DST)
    print(f"saved -> {DST}")


if __name__ == "__main__":
    main()
