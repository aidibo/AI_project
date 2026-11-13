"""t_user 表对应的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any, Optional


@dataclass
class User:
    id: Optional[int] = None
    username: str = ""
    password: str = ""
    nickname: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    age: Optional[int] = None
    gender: int = 0
    status: int = 1
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "User":
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in row.items() if k in allowed})

    def to_dict(self, exclude_none: bool = False) -> dict[str, Any]:
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        if exclude_none:
            data = {k: v for k, v in data.items() if v is not None}
        return data

    def __repr__(self) -> str:  # 便于调试打印
        return (
            f"User(id={self.id}, username={self.username!r}, "
            f"nickname={self.nickname!r}, email={self.email!r}, age={self.age}, "
            f"gender={self.gender}, status={self.status})"
        )
