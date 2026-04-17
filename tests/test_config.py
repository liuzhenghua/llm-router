from llm_router.core.config import AppMode, Settings


def test_local_database_url():
    settings = Settings(app_mode=AppMode.LOCAL, database_url=None)
    assert settings.effective_database_url.startswith("sqlite+aiosqlite:///")


def test_server_database_url():
    settings = Settings(
        app_mode=AppMode.SERVER,
        database_url=None,
        mysql_host="db",
        mysql_port=3306,
        mysql_user="user",
        mysql_password="pass",
        mysql_database="router",
    )
    assert settings.effective_database_url == "mysql+asyncmy://user:pass@db:3306/router"
