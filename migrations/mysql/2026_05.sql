-- Migration: 2026_05 (MySQL)
-- Description: (1) Add deleted_at soft-delete column to api_keys, logical_models,
--              provider_models. (2) Drop all foreign-key constraints from the
--              affected tables.
--
-- !! IMPORTANT: If you configured a custom TABLE_PREFIX (default: "llm_router_"),
-- !! replace every occurrence of "llm_router_" in this file with your prefix
-- !! before running. Example: s/llm_router_/myprefix_/g
--
-- Apply (MySQL):
--   mysql -u llm_router -p llm_router < migrations/mysql/2026_05.sql
--
-- NOTE: MySQL auto-names FK constraints as <table>_ibfk_N. If your constraints
--       have different names (check with SHOW CREATE TABLE <table>), update the
--       DROP FOREIGN KEY statements below accordingly.

SET foreign_key_checks = 0;

-- ────────────────────────────────────────────────────────────────────────────
-- 1. Add soft-delete column to the three "owner" tables
-- ────────────────────────────────────────────────────────────────────────────

ALTER TABLE llm_router_api_keys
    ADD COLUMN deleted_at DATETIME NULL DEFAULT NULL;

ALTER TABLE llm_router_logical_models
    ADD COLUMN deleted_at DATETIME NULL DEFAULT NULL;

ALTER TABLE llm_router_provider_models
    ADD COLUMN deleted_at DATETIME NULL DEFAULT NULL;

-- ────────────────────────────────────────────────────────────────────────────
-- 2. Drop foreign-key constraints
--    MySQL InnoDB auto-names FK constraints as <tablename>_ibfk_<N>.
--    Verify names with: SHOW CREATE TABLE <tablename>;
-- ────────────────────────────────────────────────────────────────────────────

-- llm_router_logical_model_routes (2 FKs: logical_model_id, provider_model_id)
ALTER TABLE llm_router_logical_model_routes
    DROP FOREIGN KEY llm_router_logical_model_routes_ibfk_1,
    DROP FOREIGN KEY llm_router_logical_model_routes_ibfk_2;

-- llm_router_request_logs (3 FKs: api_key_id, logical_model_id, provider_model_id)
ALTER TABLE llm_router_request_logs
    DROP FOREIGN KEY llm_router_request_logs_ibfk_1,
    DROP FOREIGN KEY llm_router_request_logs_ibfk_2,
    DROP FOREIGN KEY llm_router_request_logs_ibfk_3;

-- llm_router_request_log_bodies (1 FK: request_log_id)
ALTER TABLE llm_router_request_log_bodies
    DROP FOREIGN KEY llm_router_request_log_bodies_ibfk_1;

-- llm_router_usage_records (1 FK: request_log_id)
ALTER TABLE llm_router_usage_records
    DROP FOREIGN KEY llm_router_usage_records_ibfk_1;

-- llm_router_balance_ledgers (1 FK: api_key_id)
ALTER TABLE llm_router_balance_ledgers
    DROP FOREIGN KEY llm_router_balance_ledgers_ibfk_1;

-- llm_router_daily_usage_summaries (1 FK: api_key_id)
ALTER TABLE llm_router_daily_usage_summaries
    DROP FOREIGN KEY llm_router_daily_usage_summaries_ibfk_1;

SET foreign_key_checks = 1;
