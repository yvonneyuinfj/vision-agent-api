# UX Contract

## Product context

- Audience: 航空发动机无损检测工程师、复核人员与平台管理员。
- Primary jobs: 提交与追踪质检任务、人工复核、核对报告和知识依据、配置知识助手。
- Active locale: `zh-CN`；业务日期按 `Asia/Shanghai` 展示。
- Accessibility target: WCAG 2.2 AA。

## Business-context sources

| Domain / scope | Authoritative source | Source type | Reviewed date |
|---|---|---|---|
| 产品需求与用户流程 | `docs/原始材料/智能质检Agent系统_需求评估文档.md` | Product brief | 2026-08-21 |
| 知识助手配置与校验 | `app/knowledge/chat_config.py` | Domain/API contract | 2026-08-21 |
| RAGFlow 代理接口 | `app/api/v1/ragflow.py` | API contract | 2026-08-21 |

## Visual contract

- Project design context: `DESIGN.md`。
- Runtime canonical sources: `frontend/src/styles/base.css`、`frontend/src/styles/global.css`、`frontend/src/constants/theme.ts`。
- Supported themes: dark、light。
- Mapping policy: DESIGN.md 记录意图，运行时 token 为实际值的单一来源；Ant Design 只做主题适配。

## Canonical UI Map

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
|---|---|---|---|---|
| Select/Listbox | Ant Design authored primitives；知识库多选使用原生 checkbox 卡片 | DESIGN.md + component tests | authored / checkbox group | keyboard + open state |
| Form | 页面表单 + Ant Design field primitives | 本文 + API schema | create / edit | validation component test |
| Scrollbar | `frontend/src/styles/global.css` | DESIGN.md | stable-gutter exception | browser computed style |
| Dialog | Ant Design Modal | 本文 | edit / confirmation | focus + Escape + narrow viewport |
| Toast/status | 页面内持久状态；全局 HTTP 错误系统 | existing HTTP/layout behavior | success / warning / error | live status test |

## Component behavior

- 操作使用 button/link 语义，并具有 hover、focus-visible、active、disabled 与 busy 状态。
- 产品 textarea 固定 `resize: none`，长内容使用自动增高或内部滚动。
- 助手配置保存为服务端确认后的悲观更新；失败时弹窗保持打开并保留所有输入。
- 普通用户看到知识库名称、文档数、回答后果；内部 ID、模型三元组和提示词进入高级或异常诊断信息。

## Flow ledger

| Operation | Trigger | Pending | Success destination | Success feedback | Failure recovery | Focus outcome | Source ref |
|---|---|---|---|---|---|---|---|
| Edit assistant | 保存并应用 | 按钮尺寸稳定、禁止重复提交 | 关闭配置并停留在知识对话 | 顶部摘要更新 | 弹窗内显示错误并保留输入 | 返回“助手配置”触发按钮 | `app/api/v1/ragflow.py` |
| Load datasets | 打开/刷新配置 | 数据集区域稳定加载态 | 同一弹窗 | 展示可读数据集卡片 | 保留已选 ID，提供重新加载 | 数据集区域 | `GET /api/v1/ragflow/chat/datasets` |
| Auto-name session | 发送首条问题 | 不阻塞回答流 | 当前会话 | 标题替换默认名称 | 保留“新会话 + 时间”，允许手动重命名 | 对话输入保持原路径 | `PATCH /api/v1/ragflow/chat/sessions/{session_id}` |
| Rename session | 顶部“重命名会话” | 原位编辑器禁止重复保存 | 当前会话 | 侧栏与标题同步更新 | 保留输入并原位显示错误 | 返回重命名按钮 | `PATCH /api/v1/ragflow/chat/sessions/{session_id}` |
| Cancel edit | 取消 / Escape | none | 返回知识对话 | none | none | 返回触发按钮 | Ant Design Modal |

## Overlays and feedback

- Dialog primitive: Ant Design Modal，门户层、焦点圈定、Escape、滚动锁与焦点恢复由其统一负责。
- 设置弹窗不可点击遮罩误关；保存失败使用弹窗内持久错误，不用瞬时 toast 替代。
- 长弹窗正文内部滚动，标题与底部操作保持可达；窄屏转全屏单列。

## Async and resilience

- 配置保存采用悲观确认，`saving` 阻止重复提交。
- 数据集清单与对话主状态分离；清单失败不清空已有 `dataset_ids`。
- 首条问题自动命名采用本地确定性摘要，不增加模型调用；仅覆盖“新会话”默认名称，打开已有内容的旧默认会话时补齐一次，服务端失败不打断回答或历史读取。
- 手动重命名采用服务端确认后的悲观更新；Enter 保存、Escape 取消，失败保留输入。
- 后端错误保留表单上下文并允许原位重试；不向用户展示原始堆栈或凭据。

## Validation

- 表单使用 `noValidate`；名称与至少一个知识库在提交时校验。
- 错误显示在固定页脚状态区，首个无效区域获得焦点。
- 服务端继续以 Pydantic 模型为最终校验边界，特别校验知识占位符与敏感字段。

## Verification

- Required: frontend typecheck/build、Vitest、Python API tests、premium strict audit。
- Browser matrix: dark/light、桌面、窄视口、键盘、数据集 loading/empty/error、save success/error。
- Canonical sibling: 任务提交表单的字段与按钮视觉，知识对话现有错误与加载状态。
