"""全局配置（pydantic-settings，读取 .env / 环境变量）。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（app/core/config.py -> app -> 根）
BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 平台服务 ----
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"
    tool_name: str = "quality_inspection_agent"
    tool_version: str = "0.1.0"

    # ---- 持久化（唯一运行库＝MySQL，决策 50 R1：SQLite 不再是默认，仅 R3 断点库保留）----
    # 本机/内网均在 .env 覆盖成真实 DSN；这里的默认值只用于让缺 .env 时报错指向 MySQL 建库步骤。
    database_url: str = "mysql+aiomysql://qcapp:change-me@127.0.0.1:3306/quality_inspection"
    checkpointer_path: str = "./data/state.db"  # 空串 -> InMemorySaver（不跨进程）；SQLite 断点库，非任务库
    checkpointer_url: str = ""  # 非空(PG DSN)优先于 checkpointer_path：LangGraph 官方 Postgres 检查点（多 worker/多实例）
    data_dir: str = "./data"
    rules_dir: str = "./config/rules"

    # ---- 算法网关治理 ----
    gateway_request_timeout_s: float = 60.0
    gateway_retry_times: int = 2
    gateway_concurrency: int = 4
    gateway_breaker_threshold: int = 5

    # ---- RAGFlow ----
    ragflow_base_url: str = "http://localhost:9380"
    ragflow_api_key: str = ""
    ragflow_rerank_model: str = ""
    ragflow_chat_config_path: str = "./config/rules/ragflow_chat.yaml"
    ragflow_chat_startup_enabled: bool = True

    # ---- MinIO 对象存储（内容寻址去重）----
    minio_endpoint: str = ""  # 如 "localhost:9000"；空 => StorageService 停用（本地落盘兜底）
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "qc-media-assets"
    minio_secure: bool = False

    # ---- 大模型（可选；llm_provider ∈ openai_compatible / anthropic）----
    llm_provider: str = "openai_compatible"
    dashscope_api_key: str = ""
    llm_model: str = ""
    vlm_model: str = ""
    llm_base_url: str = ""
    vlm_max_tokens: int = 8192  # VLM 输出预算（thinking 输出可能很长，留足 text 生成空间）
    vlm_timeout_s: float = 300.0  # VLM 调用超时：同一网关实测 26~120s 抖动，120s 偏紧
    llm_max_retries: int = 2  # SDK 级瞬时错误（超时/限流）幂等重试次数；0 关闭
    anthropic_api_key: str = ""  # provider=anthropic 时使用（ANTHROPIC_API_KEY）
    anthropic_base_url: str = ""  # 留空走官方端点；公司代理可自定义
    scene_confirm_enabled: bool = False  # 场景多候选歧义时暂停人工确认（默认关：保持 4004 收敛）

    # ---- ReAct 智能体轨（双轨并存：mode=agent）----
    react_max_turns: int = 10  # agent 节点最大循环轮数（决策 11：正常 4-6 轮，留足自救空间）
    react_max_capability_calls: int = 3  # invoke_capability 总次数上限 = 1 + 2 次自救重试（决策 5）
    # 图片上下文策略：always=图片块每轮注入（智能体保持视觉证据用于异议比对，默认）；
    # fold=首轮场景分析后折叠历史图片块为占位文本（多模态 token 成本敏感环境用）
    react_image_context: str = "always"

    # ---- 登录认证（私有化内网访问控制）----
    # 登录账号/密码：内置随机默认值，部署时建议在 .env 用 AUTH_USERNAME / AUTH_PASSWORD 覆盖。
    # AUTH_SECRET 用于签名会话令牌（httpOnly cookie），应随机且与默认不同以保证 token 不可伪造。
    auth_username: str = "qcadmin"
    auth_password: str = "Qk9pXmVz2Lq7Wr4!FzT"  # 随机默认值（大小写+数字+安全符号，避开 # & $ 等 systemd/shell 敏感字符）；改 .env 内的 AUTH_PASSWORD 即可换密码
    auth_secret: str = "qc-aero-slideshow-7f3a2b91c6d4e8f0"  # 令牌签名密钥；生产务必覆盖
    auth_enabled: bool = True  # 置 False 关闭登录校验（保持旧行为，仅本地调试用）
    auth_cookie_name: str = "qc_session"
    auth_session_hours: float = 12.0  # 会话有效期（小时）

    # ---- 会话轨（质检助手）----
    session_max_turns: int = 6  # 会话轨单回合工具循环上限（派发+追问通常 1-2 轮）
    # HITL 复核超时扫描（秒）：awaiting_review 超过该时长标记升级提醒（仅审计
    # 留痕+告警，绝不自动裁决）；0 = 关闭。默认 3 天
    review_timeout_s: int = 259200
    # 规范 §12：日志是否记录发动机/部件编号等业务元数据（默认关，内网按需开启）
    log_business_meta: bool = False

    # ===== 便捷推导 =====
    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def rules_path(self) -> Path:
        p = Path(self.rules_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def ragflow_chat_config_file(self) -> Path:
        p = Path(self.ragflow_chat_config_path)
        return p if p.is_absolute() else BASE_DIR / p


@lru_cache
def get_settings() -> Settings:
    return Settings()
