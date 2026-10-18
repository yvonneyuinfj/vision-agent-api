-- ============================================================================
-- 平台专用 MySQL 账号（B′：平台数据复用 RAGFlow 套件的 MySQL 实例）
--
-- 说明：
--   1. 库 quality_inspection 本身由 scripts/sql/init.sql 创建（utf8mb4），本脚本只建账号授权；
--   2. 平台从宿主机连 localhost:3306（docker-proxy 转发），MySQL 容器看到的源地址是
--      docker 网桥网关（172.x.0.1）而非 localhost，因此账号 host 必须用 '%'；
--      3306 未在安全组放行，'%' 仅本机与容器网络可达，风险可控；
--   3. 勿使用 root/infini_rag_flow（RAGFlow 出厂口令）作为平台账号。
--
-- 用法（先把两处 <CHANGE_ME> 改为强密码，并与 .env 的 DATABASE_URL 保持一致）：
--   docker exec -i docker-mysql-1 mysql -uroot -p'<root密码>' < deploy/sql/create_platform_user.sql
--
-- 验证：
--   mysql -h127.0.0.1 -P3306 -uqc_agent -p quality_inspection -e 'select database(), current_user();'
-- ============================================================================

CREATE USER IF NOT EXISTS 'qc_agent'@'%' IDENTIFIED WITH caching_sha2_password BY '<CHANGE_ME>';
-- 重复执行时同步改密（密码轮换幂等）
ALTER USER 'qc_agent'@'%' IDENTIFIED WITH caching_sha2_password BY '<CHANGE_ME>';

GRANT ALL PRIVILEGES ON `quality_inspection`.* TO 'qc_agent'@'%';
FLUSH PRIVILEGES;
