from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field

from cryptography.fernet import Fernet, InvalidToken


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key(prefix: str = "lr") -> str:
    return f"{prefix}-{secrets.token_urlsafe(24)}"


def verify_api_key(raw_key: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_api_key(raw_key), stored_hash)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    payload = {
        "salt": base64.b64encode(salt).decode("ascii"),
        "digest": base64.b64encode(digest).decode("ascii"),
    }
    return json.dumps(payload, separators=(",", ":"))


def verify_password(password: str, password_hash: str) -> bool:
    try:
        payload = json.loads(password_hash)
        salt = base64.b64decode(payload["salt"])
        expected = base64.b64decode(payload["digest"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return False
    current = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return hmac.compare_digest(current, expected)


@dataclass(slots=True)
class Encryptor:
    secret: str
    _fernet: Fernet = field(init=False, repr=False)

    def __post_init__(self) -> None:
        digest = hashlib.sha256(self.secret.encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Invalid encrypted secret") from exc
