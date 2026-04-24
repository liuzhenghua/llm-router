-- Migration: 1.0.1
-- Description: <one-line summary of what this migration does>
--
-- !! IMPORTANT: If you configured a custom TABLE_PREFIX (default: "lr_"),
-- !! replace every occurrence of "lr_" in this file with your prefix
-- !! before running. Example: s/lr_/myprefix_/g
--
-- Apply (SQLite):
--   sqlite3 data/llm_router.db < migrations/1.0.1/migration_sqlite_1.0.1.sql

CREATE TABLE lr_admin_users (
	id INTEGER NOT NULL,
	username VARCHAR(100) NOT NULL,
	password_hash TEXT NOT NULL,
	is_active BOOLEAN NOT NULL,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE lr_api_keys (
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
	UNIQUE (name)
);

CREATE INDEX ix_lr_api_keys_end_user ON lr_api_keys (end_user);

CREATE INDEX ix_lr_api_keys_status ON lr_api_keys (status);

CREATE TABLE lr_balance_ledgers (
	id INTEGER NOT NULL,
	api_key_id INTEGER NOT NULL,
	change_type VARCHAR(32) NOT NULL,
	amount NUMERIC(18, 8) NOT NULL,
	balance_before NUMERIC(18, 8) NOT NULL,
	balance_after NUMERIC(18, 8) NOT NULL,
	reference_type VARCHAR(32) NOT NULL,
	reference_id VARCHAR(64) NOT NULL,
	remark VARCHAR(255),
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_lr_balance_ledgers_api_key_id ON lr_balance_ledgers (api_key_id);

CREATE INDEX ix_lr_balance_ledgers_created_at ON lr_balance_ledgers (created_at);

CREATE TABLE lr_daily_usage_summaries (
	id INTEGER NOT NULL,
	api_key_id INTEGER NOT NULL,
	summary_date DATE NOT NULL,
	request_count INTEGER NOT NULL,
	prompt_tokens INTEGER NOT NULL,
	completion_tokens INTEGER NOT NULL,
	cache_read_tokens INTEGER NOT NULL,
	cache_write_tokens INTEGER NOT NULL,
	reasoning_tokens INTEGER NOT NULL,
	cost_total NUMERIC(18, 8) NOT NULL,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_api_key_summary_date UNIQUE (api_key_id, summary_date)
);

CREATE TABLE lr_logical_model_routes (
	id INTEGER NOT NULL,
	logical_model_id INTEGER NOT NULL,
	provider_model_id INTEGER NOT NULL,
	priority INTEGER NOT NULL,
	weight INTEGER NOT NULL,
	is_fallback BOOLEAN NOT NULL,
	status VARCHAR(16) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_logical_provider_route UNIQUE (logical_model_id, provider_model_id)
);

CREATE INDEX ix_lr_logical_model_routes_logical_model_id ON lr_logical_model_routes (logical_model_id);

CREATE INDEX ix_lr_logical_model_routes_provider_model_id ON lr_logical_model_routes (provider_model_id);

CREATE TABLE lr_logical_models (
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

CREATE TABLE lr_provider_models (
	id INTEGER NOT NULL,
	name VARCHAR(100) NOT NULL,
	openai_endpoint VARCHAR(255),
	anthropic_endpoint VARCHAR(255),
	encrypted_api_key TEXT NOT NULL,
	upstream_model_name VARCHAR(120) NOT NULL,
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

CREATE TABLE lr_request_log_bodies (
	request_log_id INTEGER NOT NULL,
	request_body TEXT,
	response_body TEXT,
	PRIMARY KEY (request_log_id)
);

CREATE TABLE lr_request_logs (
	id INTEGER NOT NULL,
	request_id VARCHAR(64) NOT NULL,
	api_key_id INTEGER,
	logical_model_id INTEGER,
	provider_model_id INTEGER,
	protocol VARCHAR(32) NOT NULL,
	call_type VARCHAR(32),
	upstream_request_id VARCHAR(128),
	status_code INTEGER,
	success BOOLEAN NOT NULL,
	latency_ms INTEGER,
	error_message TEXT,
	started_at DATETIME,
	ended_at DATETIME,
	end_user VARCHAR(255),
	channel VARCHAR(64),
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_lr_request_logs_api_key_id ON lr_request_logs (api_key_id);

CREATE INDEX ix_lr_request_logs_channel ON lr_request_logs (channel);

CREATE INDEX ix_lr_request_logs_end_user ON lr_request_logs (end_user);

CREATE INDEX ix_lr_request_logs_logical_model_id ON lr_request_logs (logical_model_id);

CREATE INDEX ix_lr_request_logs_provider_model_id ON lr_request_logs (provider_model_id);

CREATE INDEX ix_lr_request_logs_started_at ON lr_request_logs (started_at);

CREATE INDEX ix_lr_request_logs_success ON lr_request_logs (success);

CREATE INDEX ix_lr_request_logs_upstream_request_id ON lr_request_logs (upstream_request_id);

CREATE TABLE lr_usage_records (
	request_log_id INTEGER NOT NULL,
	prompt_tokens INTEGER NOT NULL,
	completion_tokens INTEGER NOT NULL,
	cache_read_tokens INTEGER NOT NULL,
	cache_write_tokens INTEGER NOT NULL,
	reasoning_tokens INTEGER NOT NULL,
	input_token_price_snapshot NUMERIC(18, 8) NOT NULL,
	output_token_price_snapshot NUMERIC(18, 8) NOT NULL,
	cache_read_price_snapshot NUMERIC(18, 8) NOT NULL,
	cache_write_price_snapshot NUMERIC(18, 8) NOT NULL,
	cost_input NUMERIC(18, 8) NOT NULL,
	cost_output NUMERIC(18, 8) NOT NULL,
	cost_cache_read NUMERIC(18, 8) NOT NULL,
	cost_cache_write NUMERIC(18, 8) NOT NULL,
	cost_total NUMERIC(18, 8) NOT NULL,
	currency VARCHAR(8) NOT NULL,
	billing_date DATE NOT NULL,
	PRIMARY KEY (request_log_id)
);

CREATE INDEX ix_lr_usage_records_billing_date ON lr_usage_records (billing_date);