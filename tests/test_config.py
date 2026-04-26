from pathlib import Path

from llm_router.core.config import Settings


def test_local_database_url_uses_sqlite_path():
    settings = Settings(mysql_url="", sqlite_path=Path("/tmp/llm-router-test.db"))

    assert settings.effective_database_url == "sqlite+aiosqlite:////tmp/llm-router-test.db"


def test_server_database_url_uses_mysql_settings():
    settings = Settings(
        mysql_url="mysql://user@db:3306/router",
        mysql_password="pass",
    )

    assert settings.effective_database_url == "mysql+aiomysql://user:pass@db:3306/router"
