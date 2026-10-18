# 智能质检 Agent 前端（骨架规划，M2 起实现）

React + TypeScript + Vite + Ant Design，对接后端 `/api/v1/*`（统一外壳）。

## 目录规划

```
frontend/
├── src/
│   ├── api/            # axios 封装：统一外壳解包、trace_id 透传、401/业务码拦截
│   ├── pages/
│   │   ├── TaskSubmit/     # 任务提交：图像上传 + 场景/模式/参数表单
│   │   ├── TaskList/       # 历史任务（场景/状态筛选 + 分页）
│   │   ├── TaskDetail/     # 详情：节点时间线(stepper)、标准化结果表、判级卡片
│   │   ├── Review/         # 人工复核台：缺陷可视化、知识引用、confirm/revise/reject
│   │   ├── Report/         # 报告查看（引用可点、标注图缩略）
│   │   ├── Capabilities/   # 能力注册中心管理
│   │   └── Knowledge/      # 知识库管理（后续）
│   ├── components/     # 复核抽屉 / 引用组件 / 严重度徽标
│   ├── types/          # 与后端 schemas 对齐的 TS 类型
│   └── App.tsx
├── vite.config.ts      # dev proxy: /api → http://localhost:8000
└── package.json
```

## 关键对接契约

- 全部响应走 `{code, message, task_id, trace_id, tool, result}`；`code !== 0` 全局提示并附 trace_id；
- 任务轮询：GET /api/v1/tasks/{id}，`status == awaiting_review` 时跳转复核台；
- 复核提交：POST /api/v1/tasks/{id}/review（confirm/revise/requeue/reject + opinion）；
- 标注图/报告：GET /api/v1/tasks/{id}/files/{name}?kind=artifacts|reports；
- 轮询频率按 `/loop` 建议避开整点/半点，前端侧 3s 间隔 + 最大重试即可。