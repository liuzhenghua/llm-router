from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

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

    # Timezone for date-based calculations (billing date, daily budget reset, etc.)
    # Uses IANA timezone names, e.g. "Asia/Shanghai", "America/New_York", "UTC"
    tz: str = "UTC"

    default_request_logging_enabled: bool = False
    default_response_logging_enabled: bool = False

    session_secret: str = "change-me-session-secret"
    app_encryption_key: str = "change-me-encryption-key"

    sqlite_path: Path = DATA_DIR / "llm_router.db"
    # MySQL connection — set MYSQL_URL to switch from SQLite to MySQL
    # Format: mysql://user@host:port/database
    mysql_url: str = ""
    mysql_password: str = "llm_router"

    # Redis connection — set REDIS_URL to enable Redis cache, queue, and distributed lock
    # Format: redis://host:port/db
    redis_url: str = ""
    redis_password: str | None = None

    # 缓存 TTL 配置（秒）
    default_in_memory_ttl: int = 60    # 内存缓存 TTL
    default_redis_ttl: int = 3600      # Redis 缓存 TTL

    # 增量队列刷新间隔（秒）
    spend_queue_flush_interval: int = 30

    # Admin 列表每页数量
    admin_page_size: int = 10

    # Logging
    log_level: str = "INFO"
    log_dir: Path = DATA_DIR / "logs"

    # Optional table name prefix, e.g. "lr_" → lr_api_keys, lr_request_logs, ...
    table_prefix: str = "lr_"

    @computed_field
    @property
    def use_mysql(self) -> bool:
        return bool(self.mysql_url)

    @computed_field
    @property
    def redis_enabled(self) -> bool:
        return bool(self.redis_url)

    @computed_field
    @property
    def effective_database_url(self) -> str:
        if self.use_mysql:
            p = urlparse(self.mysql_url)
            host = p.hostname or "mysql"
            port = p.port or 3306
            user = p.username or "llm_router"
            db = p.path.lstrip("/") or "llm_router"
            return f"mysql+aiomysql://{user}:{self.mysql_password}@{host}:{port}/{db}"
        return f"sqlite+aiosqlite:///{self.sqlite_path}"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return settings


def local_date_for(tz_name: str) -> date:
    """Return today's date in the given IANA timezone name.
    Use this for per-API-key timezone-aware billing date calculations.
    Falls back to UTC if the timezone name is empty or invalid.
    """
    try:
        return datetime.now(ZoneInfo(tz_name or "UTC")).date()
    except Exception:
        return datetime.now(ZoneInfo("UTC")).date()
