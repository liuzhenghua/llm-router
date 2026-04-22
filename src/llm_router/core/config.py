from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = BASE_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "llm-router"
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # Set to True to enable Redis cache + Redis queue + distributed lock
    redis_enabled: bool = False
    # Set to True to use MySQL instead of SQLite (only used when database_url is not set)
    use_mysql: bool = False

    database_url: str | None = None
    default_request_logging_enabled: bool = False
    default_response_logging_enabled: bool = False

    admin_users_file: Path = DATA_DIR / "admin_users.json"
    session_secret: str = "change-me-session-secret"
    app_encryption_key: str = "change-me-encryption-key"

    sqlite_path: Path = DATA_DIR / "llm_router.db"
    mysql_host: str = "mysql"
    mysql_port: int = 3306
    mysql_user: str = "llm_router"
    mysql_password: str = "llm_router"
    mysql_database: str = "llm_router"

    # Redis 缓存配置（server 模式使用）
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str | None = None

    # 缓存 TTL 配置（秒）
    default_in_memory_ttl: int = 60    # 内存缓存 TTL
    default_redis_ttl: int = 3600      # Redis 缓存 TTL

    # 增量队列刷新间隔（秒）
    spend_queue_flush_interval: int = 30

    # Admin 列表每页数量
    admin_page_size: int = 10

    # Optional table name prefix, e.g. "lr_" → lr_api_keys, lr_request_logs, ...
    table_prefix: str = ""

    @computed_field
    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        if self.use_mysql:
            return (
                f"mysql+asyncmy://{self.mysql_user}:{self.mysql_password}"
                f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
            )
        return f"sqlite+aiosqlite:///{self.sqlite_path}"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings.admin_users_file.parent.mkdir(parents=True, exist_ok=True)
    return settings
