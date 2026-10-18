"""App 生命周期与依赖组装：engine/capabilities/ragflow/checkpointer/task_service 的构造入口。"""
from __future__ import annotations

import asyncio

from app.core.logger import get_logger
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.tools.assemble import assemble_tools
from app.capabilities.registry import CapabilityRegistry
from app.core.config import Settings, get_settings
from app.capabilities.adapters.base import AdapterPool
from app.knowledge.chat_config import ChatAssistantConfig, ChatConfigStore
from app.knowledge.ragflow import RAGFlowClient
from app.repositories.capability_repo import CapabilityRepo
from app.repositories.db import create_engine, create_session_factory, init_db
from app.repositories.file_repo import FileRepo
from app.repositories.session_repo import SessionRepo
from app.repositories.task_repo import TaskRepo
from app.services.deps import AgentContext
from app.agents.llm import LLMService, create_chat_model, load_system_prompt
from app.services.session_service import SessionService
from app.services.task_service import TaskService

logger = get_logger("app.deps")


class AppContainer:
    """应用组件容器：lifespan 内一次性组装，路由经 app.state.container 访问。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.engine: AsyncEngine | None = None
        self.registry: CapabilityRegistry | None = None
        self.capability_repo: CapabilityRepo | None = None
        self.ragflow: RAGFlowClient | None = None
        self.ragflow_chat_id: str | None = None
        self.ragflow_chat_config: ChatAssistantConfig | None = None
        self.ragflow_chat_error: str | None = None
        # 知识库 dataset ids 单一事实源 = config/rules/ragflow_chat.yaml 的 dataset_ids
        # （env RAGFLOW_DATASETS 已移除：检索/状态统计/文档反查直接取用，无名称→id 转换）
        self.ragflow_dataset_ids: list[str] = []
        self.ragflow_chat_store = ChatConfigStore(self.settings.ragflow_chat_config_file)
        self.llm: LLMService | None = None
        self.task_repo: TaskRepo | None = None
        self.file_repo: FileRepo | None = None
        self.storage: Any = None  # StorageService | None（未配置 MinIO 时降级）
        self.task_service: TaskService | None = None
        self.ppt_service: Any = None  # PptService（文档生成：依赖 llm/file_repo/task_repo）
        self.session_repo: SessionRepo | None = None
        self.session_service: SessionService | None = None
        self.session_bus: Any = None  # SessionEventBus（流式协议唤醒总线）
        # 编排上下文（任务轨/会话轨共用）；Studio 独立装配入口（app/studio.py）复用
        self.agent_context: AgentContext | None = None
        self.checkpointer: Any = None
        self.tools: Any = None
        self._sqlite_conn: Any = None
        self._pg_saver: Any = None
        self._pg_pool: Any = None

    async def startup(self) -> None:
        s = self.settings

        # 1. DB（SQLite 默认；生产 MySQL/PG 同路径，仅换 DATABASE_URL）
        self.engine = create_engine()
        await init_db(self.engine)

        # 2. 能力注册中心（纯 DB 驱动：capability_registry 表为唯一事实源）
        sf = create_session_factory(self.engine)
        repo = CapabilityRepo(sf)
        self.capability_repo = repo  # 写侧端点用（决策 39：列级 patch 走 repo，不走 registry）
        self.registry = CapabilityRegistry(repo)
        await self.registry.start()  # 加载全表 + 15s version 轮询

        # 3. 知识层（RAGFlow；未配置 API Key 则整体降级为无知识增强）
        self.ragflow = (
            RAGFlowClient(s.ragflow_base_url, s.ragflow_api_key)
            if s.ragflow_api_key
            else None
        )
        # dataset ids 来自 ragflow_chat.yaml（不受 chat 启动开关控制：检索链路独立依赖它）
        try:
            self.ragflow_chat_config = self.ragflow_chat_store.load()
            self.ragflow_dataset_ids = list(self.ragflow_chat_config.dataset_ids)
        except Exception as exc:  # noqa: BLE001 配置无效 → 知识增强降级，不阻断启动
            self.ragflow_chat_error = str(exc).splitlines()[0][:300]
            logger.exception("ragflow chat config load failed; knowledge retrieval degraded")
        if s.ragflow_chat_startup_enabled and self.ragflow_chat_config is not None:
            try:
                if self.ragflow is not None:
                    await self.sync_ragflow_chat(self.ragflow_chat_config, persist=False)
                else:
                    self.ragflow_chat_error = "RAGFLOW_API_KEY 未配置"
            except Exception as exc:  # noqa: BLE001 知识聊天故障不应阻断质检主服务启动
                self.ragflow_chat_error = str(exc).splitlines()[0][:300]
                logger.exception("ragflow chat initialization failed; chat feature degraded")

        # 4. LLM（可选；未配置则节点自动降级）
        #    provider=anthropic 时凭据用 anthropic_api_key（官方端点），
        #    其余走 OpenAI 兼容（DashScope/Qwen/公司网关）。
        is_anthropic = s.llm_provider == "anthropic"
        llm_key = s.anthropic_api_key if is_anthropic else s.dashscope_api_key
        if llm_key:
            base_url = (
                s.anthropic_base_url
                if is_anthropic
                else (s.llm_base_url or "https://dashscope.aliyuncs.com/api/v1")
            )
            chat_model = (
                create_chat_model(
                    s.llm_provider,
                    base_url=base_url,
                    api_key=llm_key,
                    model=s.llm_model,
                    max_retries=s.llm_max_retries,
                )
                if s.llm_model
                else None
            )
            vlm_model = (
                create_chat_model(
                    s.llm_provider,
                    base_url=base_url,
                    api_key=llm_key,
                    model=s.vlm_model,
                    max_tokens=s.vlm_max_tokens,
                    timeout_s=s.vlm_timeout_s,
                    max_retries=s.llm_max_retries,
                )
                if s.vlm_model
                else None
            )
            self.llm = LLMService(chat_model, vlm_model, load_system_prompt())

        # 5. Repo / 存储
        self.task_repo = TaskRepo(sf)
        self.file_repo = FileRepo(str(s.data_path))
        self.storage = self._build_storage(sf)
        if self.storage is not None:
            try:
                self.storage.ensure_bucket()
            except Exception:  # noqa: BLE001
                logger.exception("minio bucket ensure failed; continue with local fallback")

        # 6. 工具注册中心：DB manifest 动态挂载（孔探/红外/RAG），图节点零硬编码
        # dataset_ids 传惰性提供者：PUT /chat/config 更新后检索工具自动跟进，无需重装配
        self.tools = assemble_tools(
            self.registry, self.ragflow, settings=s,
            dataset_ids=lambda: self.ragflow_dataset_ids,
        )

        # 7. LangGraph checkpointer：人工复核(interrupt)必须持久化断点，否则恢复即失败
        self.checkpointer = await self._build_checkpointer()

        # 8. 编排服务（任务轨 + 会话轨共用同一 AgentContext / checkpointer）
        ctx = AgentContext(
            registry=self.registry,
            ragflow=self.ragflow,
            file_repo=self.file_repo,
            task_repo=self.task_repo,
            llm=self.llm,
            settings=self.settings,
            tool_registry=self.tools,
        )
        self.task_service = TaskService(ctx, repo=self.task_repo, checkpointer=self.checkpointer)
        self.agent_context = ctx

        # 8b. 文档生成服务（PPT）：复用 tasks 表与文件下载链路，LLM 缺席自动降级模板
        from app.services.ppt_service import PptService

        self.ppt_service = PptService(
            llm=self.llm, file_repo=self.file_repo, task_repo=self.task_repo
        )
        # 会话工具 generate_ppt 经 AgentContext 取用同一 PptService
        ctx.ppt_service = self.ppt_service

        # 9. 会话轨（质检助手）：会话仓储 + 会话服务 + 任务落定事件回注（决策 10）
        # 流式协议（对齐玻璃疏行演绎演示）：消息落库旁路唤醒事件总线，
        # SSE 近零延迟推帧（逐字 delta + 只推不改卡片）
        from app.services.session_stream import SessionEventBus

        self.session_repo = SessionRepo(sf)
        session_bus = SessionEventBus()
        self.session_repo.set_message_listener(session_bus.notify)
        self.session_service = SessionService(
            ctx, self.session_repo, self.task_service,
            checkpointer=self.checkpointer, event_bus=session_bus,
        )
        self.session_bus = session_bus
        self.task_service.on_settled = self.session_service.on_task_settled
        self.task_service.on_review_accepted = self.session_service.on_task_review_accepted
        # 任务步骤事件 → 会话实时活动流（优化轮 D2，append_step 旁路）
        self.task_service.on_step = self.session_service.on_task_step

        # 10. 启动唤醒对账（优化轮 E5）：停机窗口内子任务落定的联合父任务补发唤醒。
        #     后台执行（唤醒会驱动 ReAct/LLM），不阻塞服务就绪；方法内部自带异常收敛。
        asyncio.create_task(self.task_service.reconcile_joint_parents())
        # 11. 启动补卡对账：停机窗口内落定 / 功能上线前已暂停的 awaiting_review 任务，
        #     会话内缺终裁复核卡的补发（幂等；只补卡，不再触发会话智能体回合）。
        asyncio.create_task(self.session_service.reconcile_review_cards())
        # 12. HITL 复核超时扫描（review_timeout_s>0 时）：滞留复核门的任务标记
        #     升级提醒——只做审计留痕+告警，绝不自动裁决（人工终裁业务红线）。
        asyncio.create_task(self.task_service.sweep_stale_reviews())

        logger.info(
            "app container ready, capabilities=%d, enabled=%d, tools=%d, checkpointer=%s",
            len(self.registry.list()),
            len(self.registry.list_enabled()),
            len(self.tools),
            type(self.checkpointer).__name__,
        )

    async def shutdown(self) -> None:
        if self.ragflow is not None:
            await self.ragflow.aclose()
        if self.llm is not None:
            await self.llm.aclose()
        if self._pg_pool is not None:
            await self._pg_pool.close()
        if self._sqlite_conn is not None:
            await self._sqlite_conn.close()
        if self.engine is not None:
            await self.engine.dispose()
        logger.info("app container shut down")

    async def sync_ragflow_chat(
        self, config: ChatAssistantConfig, *, persist: bool = True
    ) -> dict[str, Any]:
        """创建或同步聊天助手。

        首次启动按要求 POST 创建；同名已存在时精确查询并 PATCH 同步，保证多 worker
        和进程重启不会制造重复助手。
        """
        if self.ragflow is None:
            raise RuntimeError("RAGFlow 未配置")
        payload = config.api_payload()
        chat: dict[str, Any]
        if self.ragflow_chat_id:
            chat = await self.ragflow.update_chat(self.ragflow_chat_id, payload)
            chat.setdefault("id", self.ragflow_chat_id)
        else:
            try:
                chat = await self.ragflow.create_chat(payload)
            except Exception as create_exc:  # RAGFlow 重名返回业务码 102
                existing = await self.ragflow.list_chats(name=config.name)
                chat = next((item for item in existing if item.get("name") == config.name), None) or {}
                if not chat.get("id"):
                    raise create_exc
                chat = await self.ragflow.update_chat(chat["id"], payload) or chat
        chat_id = chat.get("id")
        if not chat_id:
            raise RuntimeError("RAGFlow 创建/查询 chat 成功但未返回 id")
        if persist:
            self.ragflow_chat_store.save(config)
        self.ragflow_chat_id = str(chat_id)
        self.ragflow_chat_config = config
        self.ragflow_dataset_ids = list(config.dataset_ids)  # 运行中更新（PUT /chat/config）同步检索面
        self.ragflow_chat_error = None
        logger.info("ragflow chat ready: name=%s id=%s", config.name, chat_id)
        return chat

    def _build_storage(self, sf) -> Any:  # noqa: ANN001
        """构造 StorageService；endpoint 未配置 => 返回 None（本地落盘兜底）。"""
        s = self.settings
        if not s.minio_endpoint:
            logger.info("minio not configured; storage degraded to local file repo")
            return None
        from app.repositories.object_repo import ObjectRepo
        from app.services.storage.minio_service import StorageService

        return StorageService(
            endpoint=s.minio_endpoint,
            access_key=s.minio_access_key,
            secret_key=s.minio_secret_key,
            bucket=s.minio_bucket,
            secure=s.minio_secure,
            object_repo=ObjectRepo(sf),
        )

    async def _build_checkpointer(self) -> Any:
        """LangGraph 检查点，优先级：PG > SQLite aio > 内存。

        - checkpointer_url（PostgreSQL DSN）：官方生产后端，多 worker / 多实例
          共享检查点（SKIP LOCKED 并发取断点），人工复核(interrupt)断点持久化；
        - checkpointer_path（SQLite 文件）：单机单进程足够，跨进程安全；
        - 均未配置：MemorySaver（进程内，重启即失，测试 / 演示用）。
        """
        s = self.settings
        if s.checkpointer_url:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from psycopg_pool import AsyncConnectionPool

            # pool 生命周期由容器持有（startup 打开 / shutdown 关闭），
            # langgraph 3.x 推荐 psycopg AsyncConnectionPool 作为共享连接源；
            # autocommit 必需：langgraph setup() 迁移含 CREATE INDEX CONCURRENTLY
            pool = AsyncConnectionPool(
                s.checkpointer_url,
                open=False,
                kwargs={"autocommit": True},
            )
            await pool.open()
            saver = AsyncPostgresSaver(conn=pool)
            await saver.setup()  # 建 checkpoints / checkpoint_blobs / checkpoint_writes 表
            self._pg_saver = saver
            self._pg_pool = pool
            return saver

        if s.checkpointer_path:
            import aiosqlite
            from pathlib import Path

            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            Path(s.checkpointer_path).parent.mkdir(parents=True, exist_ok=True)
            self._sqlite_conn = await aiosqlite.connect(s.checkpointer_path)
            saver = AsyncSqliteSaver(conn=self._sqlite_conn)
            await saver.setup()
            return saver

        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()
