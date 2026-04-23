-- Migration: 2026_05 (SQLite)
-- Description: (1) Add deleted_at soft-delete column to api_keys, logical_models,
--              provider_models. (2) Drop all foreign-key constraints by recreating
--              the affected tables without FOREIGN KEY clauses. SQLite does not
--              support ALTER TABLE DROP CONSTRAINT, so each table is rebuilt.
--
-- !! IMPORTANT: If you configured a custom TABLE_PREFIX (default: "llm_router_"),
-- !! replace every occurrence of "llm_router_" in this file with your prefix
-- !! before running. Example: s/llm_router_/myprefix_/g
--
-- Apply (SQLite):
--   sqlite3 data/llm_router.db < migrations/sqlite/2026_05.sql

-- ────────────────────────────────────────────────────────────────────────────
-- 1. Add soft-delete column to the three "owner" tables
-- ────────────────────────────────────────────────────────────────────────────

ALTER TABLE llm_router_api_keys        ADD COLUMN deleted_at DATETIME NULL DEFAULT NULL;
ALTER TABLE llm_router_logical_models  ADD COLUMN deleted_at DATETIME NULL DEFAULT NULL;
ALTER TABLE llm_router_provider_models ADD COLUMN deleted_at DATETIME NULL DEFAULT NULL;

-- ────────────────────────────────────────────────────────────────────────────
-- 2. Rebuild tables that carry FOREIGN KEY constraints
--    (SQLite FK constraints are decorative when PRAGMA foreign_keys is OFF,
--     but we remove them for clarity and future-proofing.)
-- ────────────────────────────────────────────────────────────────────────────

PRAGMA foreign_keys = OFF;
BEGIN TRANSACTION;

-- ── llm_router_logical_model_routes ─────────────────────────────────────────
ALTER TABLE llm_router_logical_model_routes RENAME TO llm_router_logical_model_routes_old;

CREATE TABLE llm_router_logical_model_routes (
    id                INTEGER  NOT NULL PRIMARY KEY,
    logical_model_id  INTEGER  NOT NULL,
    provider_model_id INTEGER  NOT NULL,
    priority          INTEGER  NOT NULL,
    weight            INTEGER  NOT NULL,
    is_fallback       BOOLEAN  NOT NULL,
    status            VARCHAR(16) NOT NULL,
    CONSTRAINT uq_logical_provider_route UNIQUE (logical_model_id, provider_model_id)
);

INSERT INTO llm_router_logical_model_routes
    SELECT id, logical_model_id, provider_model_id, priority, weight, is_fallback, status
    FROM llm_router_logical_model_routes_old;

DROP TABLE llm_router_logical_model_routes_old;

-- ── llm_router_request_logs ──────────────────────────────────────────────────
ALTER TABLE llm_router_request_logs RENAME TO llm_router_request_logs_old;

CREATE TABLE llm_router_request_logs (
    id                   INTEGER     NOT NULL PRIMARY KEY,
    request_id           VARCHAR(64) NOT NULL,
    api_key_id           INTEGER,
    logical_model_id     INTEGER,
    provider_model_id    INTEGER,
    protocol             VARCHAR(32) NOT NULL,
    call_type            VARCHAR(32),
    upstream_request_id  VARCHAR(128),
    status_code          INTEGER,
    success              BOOLEAN     NOT NULL,
    latency_ms           INTEGER,
    error_message        TEXT,
    started_at           DATETIME,
    ended_at             DATETIME,
    end_user             VARCHAR(255),
    created_at           DATETIME    NOT NULL,
    UNIQUE (request_id)
);

INSERT INTO llm_router_request_logs
    SELECT id, request_id, api_key_id, logical_model_id, provider_model_id,
           protocol, call_type, upstream_request_id, status_code, success,
           latency_ms, error_message, started_at, ended_at, end_user, created_at
    FROM llm_router_request_logs_old;

DROP TABLE llm_router_request_logs_old;

-- Restore non-unique indexes for request_logs
CREATE INDEX ix_llm_router_request_logs_api_key_id        ON llm_router_request_logs (api_key_id);
CREATE INDEX ix_llm_router_request_logs_logical_model_id  ON llm_router_request_logs (logical_model_id);
CREATE INDEX ix_llm_router_request_logs_provider_model_id ON llm_router_request_logs (provider_model_id);
CREATE INDEX ix_llm_router_request_logs_upstream_request_id ON llm_router_request_logs (upstream_request_id);
CREATE INDEX ix_llm_router_request_logs_success            ON llm_router_request_logs (success);
CREATE INDEX ix_llm_router_request_logs_started_at         ON llm_router_request_logs (started_at);
CREATE INDEX ix_llm_router_request_logs_end_user           ON llm_router_request_logs (end_user);

-- ── llm_router_request_log_bodies ────────────────────────────────────────────
ALTER TABLE llm_router_request_log_bodies RENAME TO llm_router_request_log_bodies_old;

CREATE TABLE llm_router_request_log_bodies (
    request_log_id INTEGER NOT NULL,
    request_body   TEXT,
    response_body  TEXT,
    PRIMARY KEY (request_log_id)
);

INSERT INTO llm_router_request_log_bodies
    SELECT request_log_id, request_body, response_body
    FROM llm_router_request_log_bodies_old;

DROP TABLE llm_router_request_log_bodies_old;

-- ── llm_router_usage_records ─────────────────────────────────────────────────
ALTER TABLE llm_router_usage_records RENAME TO llm_router_usage_records_old;

CREATE TABLE llm_router_usage_records (
    request_log_id                INTEGER         NOT NULL,
    prompt_tokens                 INTEGER         NOT NULL,
    completion_tokens             INTEGER         NOT NULL,
    cache_read_tokens             INTEGER         NOT NULL,
    cache_write_tokens            INTEGER         NOT NULL,
    reasoning_tokens              INTEGER         NOT NULL,
    input_token_price_snapshot    NUMERIC(18, 8)  NOT NULL,
    output_token_price_snapshot   NUMERIC(18, 8)  NOT NULL,
    cache_read_price_snapshot     NUMERIC(18, 8)  NOT NULL,
    cache_write_price_snapshot    NUMERIC(18, 8)  NOT NULL,
    cost_input                    NUMERIC(18, 8)  NOT NULL,
    cost_output                   NUMERIC(18, 8)  NOT NULL,
    cost_cache_read               NUMERIC(18, 8)  NOT NULL,
    cost_cache_write              NUMERIC(18, 8)  NOT NULL,
    cost_total                    NUMERIC(18, 8)  NOT NULL,
    currency                      VARCHAR(8)      NOT NULL,
    billing_date                  DATE            NOT NULL,
    PRIMARY KEY (request_log_id)
);

INSERT INTO llm_router_usage_records
    SELECT request_log_id, prompt_tokens, completion_tokens, cache_read_tokens,
           cache_write_tokens, reasoning_tokens, input_token_price_snapshot,
           output_token_price_snapshot, cache_read_price_snapshot,
           cache_write_price_snapshot, cost_input, cost_output, cost_cache_read,
           cost_cache_write, cost_total, currency, billing_date
    FROM llm_router_usage_records_old;

DROP TABLE llm_router_usage_records_old;

CREATE INDEX ix_llm_router_usage_records_billing_date ON llm_router_usage_records (billing_date);

-- ── llm_router_balance_ledgers ───────────────────────────────────────────────
ALTER TABLE llm_router_balance_ledgers RENAME TO llm_router_balance_ledgers_old;

CREATE TABLE llm_router_balance_ledgers (
    id             INTEGER        NOT NULL PRIMARY KEY,
    api_key_id     INTEGER        NOT NULL,
    change_type    VARCHAR(32)    NOT NULL,
    amount         NUMERIC(18, 8) NOT NULL,
    balance_before NUMERIC(18, 8) NOT NULL,
    balance_after  NUMERIC(18, 8) NOT NULL,
    reference_type VARCHAR(32)    NOT NULL,
    reference_id   VARCHAR(64)    NOT NULL,
    remark         VARCHAR(255),
    created_at     DATETIME       NOT NULL
);

INSERT INTO llm_router_balance_ledgers
    SELECT id, api_key_id, change_type, amount, balance_before, balance_after,
           reference_type, reference_id, remark, created_at
    FROM llm_router_balance_ledgers_old;

DROP TABLE llm_router_balance_ledgers_old;

CREATE INDEX ix_llm_router_balance_ledgers_api_key_id  ON llm_router_balance_ledgers (api_key_id);
CREATE INDEX ix_llm_router_balance_ledgers_created_at  ON llm_router_balance_ledgers (created_at);

-- ── llm_router_daily_usage_summaries ─────────────────────────────────────────
ALTER TABLE llm_router_daily_usage_summaries RENAME TO llm_router_daily_usage_summaries_old;

CREATE TABLE llm_router_daily_usage_summaries (
    id                INTEGER        NOT NULL PRIMARY KEY,
    api_key_id        INTEGER        NOT NULL,
    summary_date      DATE           NOT NULL,
    request_count     INTEGER        NOT NULL,
    prompt_tokens     INTEGER        NOT NULL,
    completion_tokens INTEGER        NOT NULL,
    cache_read_tokens INTEGER        NOT NULL,
    cache_write_tokens INTEGER       NOT NULL,
    reasoning_tokens  INTEGER        NOT NULL,
    cost_total        NUMERIC(18, 8) NOT NULL,
    created_at        DATETIME       NOT NULL,
    updated_at        DATETIME       NOT NULL,
    CONSTRAINT uq_api_key_summary_date UNIQUE (api_key_id, summary_date)
);

INSERT INTO llm_router_daily_usage_summaries
    SELECT id, api_key_id, summary_date, request_count, prompt_tokens,
           completion_tokens, cache_read_tokens, cache_write_tokens,
           reasoning_tokens, cost_total, created_at, updated_at
    FROM llm_router_daily_usage_summaries_old;

DROP TABLE llm_router_daily_usage_summaries_old;

COMMIT;
PRAGMA foreign_keys = ON;
