from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, TypeVar

T = TypeVar("T")


class CacheSerializer:
    """
    缓存序列化器

    处理 Decimal、date、datetime 等类型的 JSON 序列化
    """

    @staticmethod
    def serialize(obj: Any) -> str:
        """对象序列化为 JSON 字符串"""
        return json.dumps(obj, default=CacheSerializer._default)

    @staticmethod
    def deserialize(data: str) -> Any:
        """JSON 字符串反序列化"""
        return json.loads(data, object_hook=CacheSerializer._object_hook)

    @staticmethod
    def _default(obj: Any) -> Any:
        """JSON 序列化默认值"""
        if isinstance(obj, Decimal):
            return {"__type__": "Decimal", "value": str(obj)}
        if isinstance(obj, date):
            return {"__type__": "date", "value": obj.isoformat()}
        if isinstance(obj, datetime):
            return {"__type__": "datetime", "value": obj.isoformat()}
        if isinstance(obj, (set, frozenset)):
            return {"__type__": "list", "value": list(obj)}
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

    @staticmethod
    def _object_hook(d: dict) -> Any:
        """JSON 反序列化钩子"""
        if "__type__" in d:
            type_name = d["__type__"]
            value = d["value"]
            if type_name == "Decimal":
                return Decimal(value)
            if type_name == "date":
                return date.fromisoformat(value)
            if type_name == "datetime":
                return datetime.fromisoformat(value)
            if type_name == "list":
                return list(value)
        return d