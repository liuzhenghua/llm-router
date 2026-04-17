from __future__ import annotations

import argparse

from llm_router.core.admin_users import AdminUserStore
from llm_router.core.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="llm-router")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_admin = subparsers.add_parser("init-admin", help="Create or update the admin user file")
    init_admin.add_argument("--username", required=True)
    init_admin.add_argument("--password", required=True)

    args = parser.parse_args()
    settings = get_settings()
    store = AdminUserStore(settings.admin_users_file)

    if args.command == "init-admin":
        store.create_or_update_user(args.username, args.password)
        print(f"Admin user saved to {settings.admin_users_file}")


if __name__ == "__main__":
    main()
