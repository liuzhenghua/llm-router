from __future__ import annotations

import argparse
import asyncio

from llm_router.core.admin_users import AdminUserService
from llm_router.core.database import SessionLocal, init_db


async def _init_admin(username: str, password: str) -> None:
    await init_db()
    async with SessionLocal() as session:
        service = AdminUserService()
        await service.create_or_update_user(session, username, password)
    print(f"Admin user '{username}' created/updated successfully.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="llm-router")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_admin = subparsers.add_parser("init-admin", help="Create or update an admin user in the database")
    init_admin.add_argument("--username", required=True)
    init_admin.add_argument("--password", required=True)

    args = parser.parse_args()

    if args.command == "init-admin":
        asyncio.run(_init_admin(args.username, args.password))


if __name__ == "__main__":
    main()
