-- Migration: 2026_04 (SQLite)
-- Description: Add encrypted_key column to api_keys — enables admins to view the
--              plaintext API key via Fernet decryption. key_hash is retained for
--              fast O(1) auth lookups.
--
-- !! IMPORTANT: If you configured a custom TABLE_PREFIX (default: "llm_router_"),
-- !! replace every occurrence of "llm_router_" in this file with your prefix
-- !! before running. Example: s/llm_router_/myprefix_/g
--
-- Apply:
--   sqlite3 data/llm_router.db < migrations/sqlite/2026_04.sql

ALTER TABLE llm_router_api_keys ADD COLUMN encrypted_key TEXT;
