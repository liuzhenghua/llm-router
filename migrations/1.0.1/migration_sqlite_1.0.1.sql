-- Migration: 1.0.1
-- Description: Add public logical model visibility flag
--
-- !! IMPORTANT: If you configured a custom TABLE_PREFIX (default: "lr_"),
-- !! replace every occurrence of "lr_" in this file with your prefix
-- !! before running. Example: s/lr_/myprefix_/g
--
-- Apply (SQLite):
--   sqlite3 data/llm_router.db < migrations/1.0.1/migration_sqlite_1.0.1.sql
--
-- Apply (MySQL):
--   mysql -u llm_router -p llm_router < migrations/1.0.1/migration_mysql_1.0.1.sql

ALTER TABLE lr_logical_models ADD COLUMN is_public INTEGER NOT NULL DEFAULT 0;
