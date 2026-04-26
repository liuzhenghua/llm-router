-- Migration: 1.0.1
-- Description: (1) Drop name UNIQUE constraints from soft-delete tables;
--              (2) Convert allowed_logical_models_json in lr_api_keys from model names to model IDs.
--
-- !! IMPORTANT: If you configured a custom TABLE_PREFIX (default: "lr_"),
-- !! replace every occurrence of "lr_" in this file with your prefix
-- !! before running. Example: s/lr_/myprefix_/g
--
-- Apply (SQLite):
--   sqlite3 data/llm_router.db < migrations/1.0.1/migration_sqlite_1.0.1.sql
--
-- === Part 1: Remove UNIQUE constraint on lr_logical_models.name ===
-- SQLite does not support DROP INDEX ... UNIQUE directly.
-- We recreate the table without the unique constraint.

PRAGMA foreign_keys = OFF;

CREATE TABLE lr_logical_models_new (
    id INTEGER NOT NULL,
    name VARCHAR(100) NOT NULL,
    description VARCHAR(255),
    routing_strategy VARCHAR(32) NOT NULL,
    is_active BOOLEAN NOT NULL,
    deleted_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id)
);

INSERT INTO lr_logical_models_new SELECT * FROM lr_logical_models;

DROP TABLE lr_logical_models;

ALTER TABLE lr_logical_models_new RENAME TO lr_logical_models;

CREATE INDEX ix_lr_logical_models_name ON lr_logical_models (name);

-- === Part 2: Remove UNIQUE constraint on lr_api_keys.name ===
-- SQLite does not support DROP INDEX ... UNIQUE directly.
-- We recreate the table without the unique constraint on name and keep UNIQUE(key_hash).

CREATE TABLE lr_api_keys_new (
    id INTEGER NOT NULL,
    name VARCHAR(100) NOT NULL,
    key_hash VARCHAR(64) NOT NULL,
    encrypted_key TEXT,
    status VARCHAR(16) NOT NULL,
    balance NUMERIC(18, 6) NOT NULL,
    daily_budget_limit NUMERIC(18, 6),
    daily_spend_amount NUMERIC(18, 6) NOT NULL,
    daily_spend_date DATE,
    qps_limit INTEGER NOT NULL,
    allowed_logical_models_json TEXT NOT NULL,
    request_content_logging_enabled BOOLEAN,
    response_content_logging_enabled BOOLEAN,
    end_user VARCHAR(255),
    timezone VARCHAR(64) NOT NULL,
    default_channel VARCHAR(64),
    deleted_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (key_hash)
);

INSERT INTO lr_api_keys_new SELECT * FROM lr_api_keys;

DROP TABLE lr_api_keys;

ALTER TABLE lr_api_keys_new RENAME TO lr_api_keys;

CREATE INDEX ix_lr_api_keys_name ON lr_api_keys (name);
CREATE INDEX ix_lr_api_keys_end_user ON lr_api_keys (end_user);
CREATE INDEX ix_lr_api_keys_status ON lr_api_keys (status);

-- === Part 3: Add index on lr_provider_models.name ===
CREATE INDEX ix_lr_provider_models_name ON lr_provider_models (name);

PRAGMA foreign_keys = ON;
