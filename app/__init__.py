"""智能质检 Agent 系统后端（FastAPI + LangGraph + RAGFlow）。

分层约定（严格，禁止层级穿透）：
- api/      接口层：路由分发、Pydantic 参数校验、统一响应外壳
- services/ 服务层：LangGraph 编排、规则引擎、LLM、网关调用
- gateway/  AI 网关：异构算法工具适配与治理
- knowledge/知识层：RAGFlow 检索客户端
- repositories/ 持久层：DB / 文件 / artifact
"""