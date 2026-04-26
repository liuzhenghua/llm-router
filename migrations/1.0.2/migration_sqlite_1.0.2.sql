-- Migration: 1.0.2
-- Description: Add description column to lr_provider_models table
--
-- !! IMPORTANT: If you configured a custom TABLE_PREFIX (default: "lr_"),
-- !! replace every occurrence of "lr_" in this file with your prefix
-- !! before running. Example: s/lr_/myprefix_/g
--
-- Apply (SQLite):
--   sqlite3 data/llm_router.db < migrations/1.0.2/migration_sqlite_1.0.2.sql

PRAGMA foreign_keys = OFF;

CREATE TABLE lr_provider_models_new (
    id INTEGER NOT NULL,
    name VARCHAR(100) NOT NULL,
    openai_endpoint VARCHAR(255),
    anthropic_endpoint VARCHAR(255),
    encrypted_api_key TEXT NOT NULL,
    upstream_model_name VARCHAR(120) NOT NULL,
    description VARCHAR(255),
    input_token_price NUMERIC(18, 8) NOT NULL,
    output_token_price NUMERIC(18, 8) NOT NULL,
    supports_prompt_cache BOOLEAN NOT NULL,
    cache_read_token_price NUMERIC(18, 8) NOT NULL,
    cache_write_token_price NUMERIC(18, 8) NOT NULL,
    is_active BOOLEAN NOT NULL,
    timeout_seconds INTEGER NOT NULL,
    deleted_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id)
);

INSERT INTO lr_provider_models_new SELECT id, name, openai_endpoint, anthropic_endpoint, encrypted_api_key, upstream_model_name, NULL, input_token_price, output_token_price, supports_prompt_cache, cache_read_token_price, cache_write_token_price, is_active, timeout_seconds, deleted_at, created_at, updated_at FROM lr_provider_models;

DROP TABLE lr_provider_models;

ALTER TABLE lr_provider_models_new RENAME TO lr_provider_models;

CREATE INDEX ix_lr_provider_models_name ON lr_provider_models (name);

PRAGMA foreign_keys = ON;
