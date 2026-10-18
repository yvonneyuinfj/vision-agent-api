-- ============================================================================
-- 智能质检 Agent 系统 —— 数据库初始化脚本（MySQL 8.x）
--
-- 强制规范（对齐重构指令）：
--   1. 数据库与全部表使用 utf8mb4 字符集、utf8mb4_general_ci 排序规则；
--   2. 每个表 / 每个字段必须带 COMMENT 说明业务用途；
--   3. 本目录为 DDL/DML 唯一权威出处，严禁在代码中硬编码建表语句；
--   4. 参与排序的时间列一律 DATETIME(6)：消息 id / 任务号都是随机串，秒级精度会让
--      同秒写入的时序塌缩成随机序（ORM 的 _dt6() 声明以此为准，两侧不一致时 DDL 输）。
--
-- 用法：
--   mysql -u root -p < scripts/sql/init.sql
--   # 或指定库（仅当 init.sql 落库后）：
--   mysql -h <host> -u <user> -p quality_inspection < scripts/sql/init.sql --skip-database-create
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. 业务数据库（字符集/排序规则强制 utf8mb4 / utf8mb4_general_ci）
-- ---------------------------------------------------------------------------
CREATE DATABASE IF NOT EXISTS `quality_inspection`
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_general_ci;

USE `quality_inspection`;

-- ---------------------------------------------------------------------------
-- 2. 任务表：任务状态机 + 中间结果 JSON 快照（全链路留痕 / 追溯 / 报告引用）
--    对应 app/repositories/task_repo.py::TaskRecord
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `tasks` (
    `id`             VARCHAR(64)  NOT NULL COMMENT '任务号，如 T18x...（主键）',
    `trace_id`       VARCHAR(64)  NULL     COMMENT '链路追踪 ID（统一外壳透传）',
    `scene`          VARCHAR(32)  NULL     COMMENT '检测场景编码：borescope/infrared/dr/fpi/fracture/metallography',
    `mode`           VARCHAR(16)  NOT NULL DEFAULT 'single' COMMENT '执行模式：single 单场景 / joint 多场景联合',
    `status`         VARCHAR(24)  NOT NULL DEFAULT 'pending' COMMENT '任务状态机：pending/running/awaiting_review/completed/failed/canceled',
    `capability_used` VARCHAR(64)  NULL     COMMENT '实际调用的算法能力（tool_name，调用追踪用）',
    `inputs_json`    TEXT         NULL     COMMENT '输入引用：原图/CSV 等落盘 URI 与参数（JSON）',
    `raw_results_json` TEXT       NULL     COMMENT '算法工具原始回复外壳（JSON，审计保留）',
    `standardized_json` TEXT      NULL     COMMENT '标准化结果：缺陷/测量/分类分型（JSON）',
    `class_definitions_json` TEXT NULL     COMMENT '工具类别定义表：类别代码→中文名+真缺陷/虚假显示语义（JSON）',
    `knowledge_json` TEXT         NULL     COMMENT '知识增强：检索引用 + 解读（JSON）',
    `grade_json`     TEXT         NULL     COMMENT '判级结论：level/summary/规则命中（JSON）',
    `review_json`    TEXT         NULL     COMMENT '人工复核：问题与专家意见（JSON，HITL 留痕）',
    `report_json`    TEXT         NULL     COMMENT '报告引用（报告类型/文件 URI/版本）',
    `steps_json`     TEXT         NOT NULL COMMENT '节点执行追溯记录（JSON 数组）',
    `error_json`     TEXT         NULL     COMMENT '失败快照：业务码 + 消息（JSON）',
    `created_at`     DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间（UTC）',
    `updated_at`     DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '最近更新时间（UTC）',
    `finished_at`    DATETIME(6)  NULL     COMMENT '完成/失败时间（UTC）',
    `node_index`     INT          NOT NULL DEFAULT 0 COMMENT '节点执行计数（追溯 seq）',
    PRIMARY KEY (`id`),
    INDEX `idx_tasks_status` (`status`),
    INDEX `idx_tasks_scene` (`scene`),
    INDEX `idx_tasks_created_at` (`created_at`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_general_ci
  COMMENT = '质检任务主表：状态机 + 全链路 JSON 快照（追溯底座）';

-- ---------------------------------------------------------------------------
-- 3. 能力注册中心表：算法能力动态注册（DB 驱动，纯 DB 事实源）
--    「启停控制」：status 三态 enabled/disabled/maintenance + idx_scene_status 路由索引
--    「自动刷新」：version 自增，平台 15s 轮询 MAX(version) 比对后重载缓存
--    对应 app/capabilities/registry.py + app/repositories/capability_repo.py
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `capability_registry` (
    `id`              BIGINT       NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `capability_id`   VARCHAR(128) NOT NULL COMMENT '能力唯一ID（如 borescope_defect_detection_v1）',
    `tool_name`       VARCHAR(128) NOT NULL COMMENT '算法工具名称（Tool 挂载名 detect/<tool_name>）',
    `scene`           VARCHAR(64)  NOT NULL COMMENT '适用检测场景编码（路由键）',
    `capability_type` VARCHAR(32)  NOT NULL DEFAULT 'defect_detection' COMMENT '能力类型：defect_detection/measurement/classification（Normalizer 分流键）',
    `adapter_type`    VARCHAR(32)  NOT NULL DEFAULT 'http' COMMENT '接入形态：http/model_service/local_bridge',
    `endpoint`        VARCHAR(512) NOT NULL COMMENT '推理服务地址（http 用）',
    `health_endpoint` VARCHAR(512) NULL     COMMENT '健康检查地址（http 用）',
    `tool_version`    VARCHAR(64)  NOT NULL COMMENT '工具版本',
    `model_version`   VARCHAR(64)  NOT NULL COMMENT '模型版本',
    `api_version`     VARCHAR(32)  NOT NULL DEFAULT 'v1' COMMENT '接口版本',
    `input_schema`    JSON         NULL     COMMENT '输入参数结构说明（JSON Schema）',
    `output_schema`   JSON         NULL     COMMENT '输出结果结构说明（JSON Schema）',
    `timeout_s`       INT          NOT NULL DEFAULT 60 COMMENT '网关调用超时（秒）',
    `max_retry`       INT          NOT NULL DEFAULT 2 COMMENT '网关最大重试次数',
    `status`          VARCHAR(24)  NOT NULL DEFAULT 'enabled' COMMENT '能力状态：enabled/disabled/maintenance（启停控制）',
    `description`     TEXT         NULL     COMMENT '能力说明（展示/审计用）',
    `model_weights_sha256` CHAR(64) NULL     COMMENT '模型权重 SHA256（规范 §9 准入校验，模型文件到位后回填）',
    `meta_json`       TEXT         NULL     COMMENT '平台扩展：default_params/input_required/max_images 等（JSON）',
    `version`         INT          NOT NULL DEFAULT 1 COMMENT '版本号：人工注册/更新时自增（轮询刷新比对键）',
    `created_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '注册时间（UTC）',
    `updated_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最近更新时间（UTC）',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_capability_id` (`capability_id`),
    KEY `idx_scene_status` (`scene`, `status`),
    KEY `idx_cap_tool_name` (`tool_name`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_general_ci
  COMMENT = '能力注册中心表：能力准入/启停/路由/版本统一依据（DB 驱动事实源）';

-- ---------------------------------------------------------------------------
-- 4. 文件对象表：内容寻址去重（Hash -> URI 映射）
--    「秒传」判定依据：同一 SHA-256 只落一份对象，重复上传仅引用计数 +1
--    对应 app/repositories/object_repo.py::FileObjectRecord
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `file_objects` (
    `sha256`         CHAR(64)     NOT NULL COMMENT '文件内容 SHA-256（内容寻址主键，秒传判定键）',
    `bucket`         VARCHAR(64)  NOT NULL DEFAULT 'qc-media-assets' COMMENT '对象存储桶名（MinIO）',
    `object_name`    VARCHAR(255) NOT NULL COMMENT '对象名：{sha256}.{ext}（内容寻址防碰撞）',
    `uri`            VARCHAR(512) NOT NULL COMMENT '对外引用 URI：minio://bucket/object_name',
    `ext`            VARCHAR(16)  NULL     COMMENT '文件扩展名（不含点，未知则 NULL）',
    `size_bytes`     BIGINT       NULL     COMMENT '文件大小（字节，秒传/BOM 查询用）',
    `content_type`   VARCHAR(64)  NULL     COMMENT 'MIME 类型（上传时推断）',
    `first_task_id`  VARCHAR(64)  NULL     COMMENT '首次入库的任务号（来源追溯）',
    `last_task_id`   VARCHAR(64)  NULL     COMMENT '最近一次引用该对象的任务号',
    `ref_count`      INT          NOT NULL DEFAULT 1 COMMENT '引用计数：同内容复用次数（秒传统计）',
    `meta_json`      TEXT         NULL     COMMENT '扩展元数据：来源/用途等（JSON）',
    `created_at`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '首次入库时间（UTC）',
    `updated_at`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最近引用时间（UTC）',
    PRIMARY KEY (`sha256`),
    INDEX `idx_file_objects_object_name` (`object_name`),
    INDEX `idx_file_objects_uri` (`uri`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_general_ci
  COMMENT = '文件对象内容寻址表：MinIO 文件 Hash 与 URI 映射（去重秒传依据）';

-- ---------------------------------------------------------------------------
-- 5.（规划）知识文档表：M2 知识增强全链路（RAGFlow + MinerU 入库清单）
--    本期仅建骨架空表，字段随知识接入补充
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `knowledge_docs` (
    `id`             BIGINT       NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `dataset_name`   VARCHAR(128) NOT NULL COMMENT 'RAGFlow 数据集名称',
    `doc_name`       VARCHAR(255) NOT NULL COMMENT '原始文档名（上传/解析来源）',
    `doc_id`         VARCHAR(64)  NULL     COMMENT 'RAGFlow 侧文档 ID（异步删除/收敛用）',
    `parser_status`  VARCHAR(24)  NOT NULL DEFAULT 'pending' COMMENT '解析状态：pending/parsing/ready/failed',
    `mineru_triplet` VARCHAR(128) NULL     COMMENT 'MinerU 解析器三元组（layout_recognize 配置）',
    `created_at`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间（UTC）',
    `updated_at`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最近更新时间（UTC）',
    PRIMARY KEY (`id`),
    INDEX `idx_knowledge_docs_dataset` (`dataset_name`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_general_ci
  COMMENT = '知识文档入库清单：RAGFlow 数据集与 MinerU 解析状态追踪';
-- ---------------------------------------------------------------------------
-- 6. 质检助手会话（会话轨）：会话 + 消息两表
--    - agent_sessions：一次会话（LangGraph thread_id 即 session_id）
--    - session_messages：会话消息流（user/assistant/event；文本/图片/任务卡片）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent_sessions` (
    `id`         VARCHAR(64)  NOT NULL COMMENT '会话 ID（S 前缀，同时作为 LangGraph thread_id）',
    `title`      VARCHAR(256) NULL     COMMENT '会话标题（用户可改）',
    `status`     VARCHAR(16)  NOT NULL DEFAULT 'active' COMMENT '会话状态：active/archived',
    `created_at` DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间（UTC）',
    `updated_at` DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '最近活跃时间（UTC）',
    PRIMARY KEY (`id`),
    INDEX `idx_agent_sessions_updated` (`updated_at`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_general_ci
  COMMENT = '质检助手会话：会话轨（mode=agent）的对话容器';

CREATE TABLE IF NOT EXISTS `session_messages` (
    `id`          VARCHAR(64) NOT NULL COMMENT '消息 ID',
    `session_id`  VARCHAR(64) NOT NULL COMMENT '所属会话 ID',
    `role`        VARCHAR(16) NOT NULL COMMENT '消息角色：user/assistant/event',
    `kind`        VARCHAR(24) NOT NULL DEFAULT 'text' COMMENT '消息形态：text/image/task_card/guidance/event',
    `content`     TEXT        NOT NULL COMMENT '消息文本内容',
    `images_json` TEXT        NULL     COMMENT '附带图片引用（JSON 数组：uri/name/media_type）',
    `task_id`     VARCHAR(64) NULL     COMMENT '关联任务卡片（任务落定事件按此回注定位会话）',
    `created_at`  DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间（UTC）',
    PRIMARY KEY (`id`),
    INDEX `idx_session_messages_session` (`session_id`, `created_at`),
    INDEX `idx_session_messages_task` (`task_id`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_general_ci
  COMMENT = '会话消息流：用户消息/助手回复/任务事件（含任务卡片与引导消息）';
