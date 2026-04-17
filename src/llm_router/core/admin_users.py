from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from llm_router.core.security import hash_password, verify_password


class AdminUserStore:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path

    def exists(self) -> bool:
        return self.file_path.exists()

    def load(self) -> dict[str, Any]:
        if not self.file_path.exists():
            return {"users": []}
        return json.loads(self.file_path.read_text(encoding="utf-8"))

    def save(self, payload: dict[str, Any]) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def create_or_update_user(self, username: str, password: str, is_active: bool = True) -> None:
        payload = self.load()
        users = payload.setdefault("users", [])
        password_hash = hash_password(password)
        for user in users:
            if user["username"] == username:
                user["password_hash"] = password_hash
                user["is_active"] = is_active
                self.save(payload)
                return
        users.append(
            {
                "username": username,
                "password_hash": password_hash,
                "is_active": is_active,
            }
        )
        self.save(payload)

    def authenticate(self, username: str, password: str) -> bool:
        payload = self.load()
        for user in payload.get("users", []):
            if user.get("username") != username:
                continue
            if not user.get("is_active", True):
                return False
            return verify_password(password, user.get("password_hash", ""))
        return False
