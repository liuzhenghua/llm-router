-- Migration: 1.0.1
-- Description: (1) Drop name UNIQUE constraints from soft-delete tables;
--              (2) Convert allowed_logical_models_json in lr_api_keys from model names to model IDs.
--
-- !! IMPORTANT: If you configured a custom TABLE_PREFIX (default: "lr_"),
-- !! replace every occurrence of "lr_" in this file with your prefix
-- !! before running. Example: s/lr_/myprefix_/g
--
-- Apply (MySQL):
--   mysql -u llm_router -p llm_router < migrations/1.0.1/migration_mysql_1.0.1.sql
--
-- === Part 1: Remove UNIQUE constraint on lr_logical_models.name ===

ALTER TABLE lr_logical_models DROP INDEX name;
CREATE INDEX ix_lr_logical_models_name ON lr_logical_models (name);

-- === Part 2: Remove UNIQUE constraint on lr_api_keys.name ===

ALTER TABLE lr_api_keys DROP INDEX name;
CREATE INDEX ix_lr_api_keys_name ON lr_api_keys (name);

-- === Part 3: Add index on lr_provider_models.name ===
CREATE INDEX ix_lr_provider_models_name ON lr_provider_models (name);
