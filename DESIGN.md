---
version: alpha
name: "AeroQC"
description: "面向航空发动机无损检测工程师的工业质检工作台，以仪器校准感和证据可追溯性组织高密度信息。"
colors:
  background-dark: "oklch(16.5% 0.02 255)"
  foreground-dark: "oklch(95% 0.006 250)"
  primary: "oklch(78% 0.12 195)"
  accent-light: "oklch(58% 0.12 200)"
  info: "oklch(72% 0.14 250)"
  success: "oklch(76% 0.15 155)"
  warning: "oklch(80% 0.15 75)"
  danger: "oklch(68% 0.19 25)"
typography:
  sans:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro SC, PingFang SC, HarmonyOS Sans SC, Microsoft YaHei, system-ui, sans-serif"
  mono:
    fontFamily: "JetBrains Mono, ui-monospace, SF Mono, Menlo, Consolas, monospace"
rounded:
  sm: "0.5rem"
  DEFAULT: "0.625rem"
  lg: "0.875rem"
spacing:
  page-inline: "1.625rem"
  section-gap: "1.5rem"
  control-height: "2.25rem"
components:
  button:
    backgroundColor: "$colors.primary"
    textColor: "$colors.background-dark"
    rounded: "$rounded.sm"
    height: "$spacing.control-height"
  card:
    backgroundColor: "$colors.background-dark"
    textColor: "$colors.foreground-dark"
    rounded: "$rounded.lg"
  dialog:
    backgroundColor: "$colors.background-dark"
    textColor: "$colors.foreground-dark"
    rounded: "$rounded.lg"
  input:
    backgroundColor: "$colors.background-dark"
    textColor: "$colors.foreground-dark"
    rounded: "$rounded.sm"
    height: "$spacing.control-height"
---

# AeroQC Design System

## Overview

### Creative North Star

界面应像一台经过校准的无损检测仪：安静、可信、读数清楚，关键状态像仪器刻度一样有明确含义。玻璃层与微弱网格提供航空工业语境，但不遮挡业务信息。

### Product context and register

- **用户与任务：** 质检工程师和复核人员提交检测任务、追踪流水线、复核缺陷并从本地知识库核对判级依据。
- **市场与语言：** 当前证据为中国航空发动机质检场景，界面使用简体中文；产品文案优先使用业务概念，不向普通用户暴露内部 ID。
- **使用场景：** 桌面工作站上的高频、偏高密度操作，关键结论需要可追溯且适合复核。
- **表现类型：** 产品型界面。熟悉、稳定和可判断性优先于品牌展示。
- **记忆点：** 用“校准刻度”表达置信度、回答风格和处理进度；同一屏只允许一个这样的强调元素。
- **克制：** 表单、表格、确认和错误恢复保持安静直白，不使用装饰性大标题、无意义渐变或营销式文案。
- **反例：** 不做通用紫色 SaaS 模板，不做消费级聊天玩具，也不把 JSON、数据库 ID 或模型参数当成普通用户语言。
- **Token 所有权：** 运行时以 [frontend/src/styles/base.css](frontend/src/styles/base.css) 与 [frontend/src/constants/theme.ts](frontend/src/constants/theme.ts) 为权威；本文记录其设计意图与稳定映射，主题适配不得复制出第三套数值。

## Colors

深色主题使用冷蓝黑背景和低饱和玻璃表面；青色只表示当前、选中和主操作。成功、警告、危险色是语义色，必须同时配合文字或图标。浅色主题改变背景与前景对比，不改变语义层级。焦点环使用 accent，保证键盘路径可见。

## Typography

中文界面使用系统中文无衬线栈，正文保持自然中文语序；内部标识、数值、版本和刻度使用等宽字体。控件不使用全大写英文，短英文眉题只作为辅助仪器标签。长说明控制行宽，避免连续的灰色小字。

## Layout

桌面主框架由 232px 可折叠侧栏、60px 顶栏和自适应内容区组成。页面内信息以 14–26px 间距形成紧凑节奏。长表单保持自然内容顺序；长对话、列表和弹窗正文可拥有明确的内部滚动区，标题与操作保持可达。配置表单在窄屏降为单列。

## Elevation & Depth

层级主要由透明度、边框和背景明度区分。静态卡片只使用现有轻阴影；对话框可以使用更强阴影和遮罩。嵌套表单区不重复堆叠阴影，选中状态用边框与浅色填充表达。

## Shapes

容器采用 14px 圆角，输入和按钮采用 8–10px 圆角；状态标签可使用胶囊形。图标统一使用 1.7–1.8px 线性描边。圆角用于组织触控区域，不把每一段文字包成卡片。

## Components

### Foundational visual states

所有操作需覆盖默认、悬停、键盘焦点、按下、禁用和忙碌状态。选中不能只靠颜色；错误保留用户输入并给出恢复路径。加载区域预留稳定几何，不用闪烁骨架屏。

### Buttons and actions

一个决策区只保留一个主操作。安全主操作使用 accent，取消与工具操作使用中性样式，危险操作使用 danger 且与安全操作拉开距离。忙碌时按钮尺寸不变。

### Navigation and data display

侧栏、面包屑、标签和状态词保持全局一致。表格与对话引用优先支持扫描和比对；截断的重要值必须可通过标题、详情或展开方式读取。

### Forms and overlays

字段名称使用用户能控制的结果，技术参数放入高级折叠区。选择知识库时展示名称、描述和文档量，内部 ID 仅用于异常诊断。对话框使用项目已接入的 Ant Design 可访问弹层，正文内部滚动，页脚操作固定可达。

### Iconography

使用项目内统一的线性 SVG 与 Ant Design 基础图标。图标不能替代不熟悉操作的文字标签；纯图标按钮必须有中文可访问名称。

### Motion

动效只解释状态变化，通常为 150–300ms，并支持中途反转。遵守 `prefers-reduced-motion`；持续脉冲仅用于真实在线或运行状态。

### Content and data visualization

文案直接说明操作结果和风险。检索阈值等参数翻译成“资料更多 / 只保留高相关资料”等后果；必要的精确数值仍可见。质量结论不使用拟人化或保证式措辞。

## Do's and Don'ts

- **Do:** 先展示业务名称、状态与后果，再提供内部标识作为辅助诊断信息。
- **Do:** 用同一套校准色和刻度语言表达模型行为、置信度与进度。
- **Don't:** 把 JSON、数据集 ID、模型三元组或 temperature 直接作为默认配置界面。
- **Don't:** 为追求科技感增加不可读的小字、无语义光晕或多处竞争性动效。
