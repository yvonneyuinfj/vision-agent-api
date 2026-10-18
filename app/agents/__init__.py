"""编排层：LangGraph 状态图（8 生产节点 + 人工复核 gate）+ Tool 注册中心。

├── state.py / graph.py    AgentState + StateGraph 装配
├── nodes/                 编排节点（intent/route/validate/infer/normalize/...）
└── tools/                 Tool 注册中心（registry/assemble/infer_tool/rag_tool）
                           依赖注入经 AgentContext 下发，节点不直接访问 DB/文件。
"""