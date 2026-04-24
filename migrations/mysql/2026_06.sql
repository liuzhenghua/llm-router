-- Migration: 2026_06
-- Description: Add per-API-key timezone field for billing date calculation
--
-- !! IMPORTANT: If you configured a custom TABLE_PREFIX (default: "llm_router_"),
-- !! replace every occurrence of "llm_router_" in this file with your prefix
-- !! before running. Example: s/llm_router_/myprefix_/g
--
-- Apply (MySQL):
--   mysql -u llm_router -p llm_router < migrations/mysql/2026_06.sql

ALTER TABLE llm_router_api_keys
    ADD COLUMN timezone VARCHAR(64) NOT NULL DEFAULT 'UTC';
