from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = BASE_DIR / "data"


class AppMode(StrEnum):
    LOCAL = "local"
    SERVER = "server"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "llm-router"
    app_mode: AppMode = AppMode.LOCAL
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

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

    @computed_field
    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        if self.app_mode == AppMode.SERVER:
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
