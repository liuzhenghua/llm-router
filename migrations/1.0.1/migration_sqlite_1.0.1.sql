-- Migration: 1.0.1
-- Description: (1) Drop UNIQUE constraint on lr_logical_models.name to allow duplicate names;
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

PRAGMA foreign_keys = ON;

-- === Part 2: Data migration — allowed_logical_models_json names → IDs ===
-- The application performs this automatically on first startup.
-- If you prefer to do it manually, see the Python snippet below:
--
--   python - <<'EOF'
--   import asyncio
--   from sqlalchemy import select
--   from llm_router.core.database import SessionLocal
--   from llm_router.domain.models import ApiKey, LogicalModel
--
--   async def migrate():
--       async with SessionLocal() as session:
--           models = (await session.execute(select(LogicalModel))).scalars().all()
--           name_to_id = {m.name: m.id for m in models}
--           keys = (await session.execute(select(ApiKey))).scalars().all()
--           for key in keys:
--               values = key.allowed_logical_models_json or []
--               if values and any(isinstance(v, str) for v in values):
--                   key.allowed_logical_models_json = [name_to_id[v] for v in values if isinstance(v, str) and v in name_to_id]
--           await session.commit()
--           print("Migration complete")
--
--   asyncio.run(migrate())
-- EOF
