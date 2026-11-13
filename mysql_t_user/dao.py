"""t_user 表的数据访问对象（DAO），演示常用增删改查操作。

所有 SQL 均使用参数化占位符 %s，避免 SQL 注入。
"""

from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

from .db import Database
from .models import User

TABLE = "t_user"

# 允许写入的字段白名单，防止字段名拼接被注入
_WRITABLE = ("username", "password", "nickname", "email", "phone", "age", "gender", "status")


class UserDao:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ================================================================ 建表
    def create_table(self, sql_file: str = "schema.sql") -> None:
        """执行建表脚本（幂等，使用 IF NOT EXISTS）。"""
        import pathlib

        path = pathlib.Path(sql_file)
        if not path.is_absolute():
            path = pathlib.Path(__file__).parent / sql_file
        for stmt in self._split_sql(path.read_text(encoding="utf-8")):
            self.db.execute(stmt)

    @staticmethod
    def _split_sql(script: str) -> list[str]:
        lines = [
            ln for ln in script.splitlines() if ln.strip() and not ln.strip().startswith("--")
        ]
        return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]

    # ================================================================ 增
    def insert(self, user: User) -> int:
        """插入一条用户，返回自增主键 id。"""
        data = {k: getattr(user, k) for k in _WRITABLE if getattr(user, k, None) is not None}
        columns = ", ".join(f"`{c}`" for c in data)
        placeholders = ", ".join(["%s"] * len(data))
        sql = f"INSERT INTO `{TABLE}` ({columns}) VALUES ({placeholders})"
        with self.db.cursor(commit=True) as cur:
            cur.execute(sql, list(data.values()))
            return int(cur.lastrowid)

    def insert_many(self, users: Iterable[User]) -> int:
        """批量插入，返回成功写入的行数。"""
        users = list(users)
        if not users:
            return 0
        sql = (
            f"INSERT INTO `{TABLE}` "
            "(`username`, `password`, `nickname`, `email`, `phone`, `age`, `gender`, `status`) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
        )
        rows = [
            (
                u.username,
                u.password,
                u.nickname,
                u.email,
                u.phone,
                u.age,
                u.gender,
                u.status,
            )
            for u in users
        ]
        return self.db.executemany(sql, rows)

    def upsert(self, user: User) -> None:
        """按唯一键 username 插入或更新（ON DUPLICATE KEY UPDATE）。"""
        # 未传入（NULL）的字段保留原值，避免 upsert 时被清空
        sql = (
            f"INSERT INTO `{TABLE}` "
            "(`username`, `password`, `nickname`, `email`, `phone`, `age`, `gender`, `status`) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE "
            "`password`=VALUES(`password`), `nickname`=COALESCE(VALUES(`nickname`), `nickname`), "
            "`email`=COALESCE(VALUES(`email`), `email`), "
            "`phone`=COALESCE(VALUES(`phone`), `phone`), "
            "`age`=COALESCE(VALUES(`age`), `age`), "
            "`gender`=VALUES(`gender`), `status`=VALUES(`status`)"
        )
        self.db.execute(
            sql,
            (
                user.username,
                user.password,
                user.nickname,
                user.email,
                user.phone,
                user.age,
                user.gender,
                user.status,
            ),
        )

    # ================================================================ 删
    def delete_by_id(self, user_id: int) -> int:
        """按主键删除，返回受影响行数。"""
        return self.db.execute(f"DELETE FROM `{TABLE}` WHERE `id` = %s", (user_id,))

    def delete_by_username(self, username: str) -> int:
        return self.db.execute(f"DELETE FROM `{TABLE}` WHERE `username` = %s", (username,))

    def delete_by_ids(self, ids: Sequence[int]) -> int:
        """按主键列表批量删除。"""
        if not ids:
            return 0
        placeholders = ", ".join(["%s"] * len(ids))
        return self.db.execute(
            f"DELETE FROM `{TABLE}` WHERE `id` IN ({placeholders})", tuple(ids)
        )

    def delete_all(self) -> int:
        """清空表数据（保留自增计数）。"""
        return self.db.execute(f"DELETE FROM `{TABLE}`")

    # ================================================================ 改
    def update(self, user_id: int, **changes: Any) -> int:
        """按主键局部更新，仅更新传入的字段。"""
        data = {k: v for k, v in changes.items() if k in _WRITABLE}
        if not data:
            raise ValueError("没有可更新的字段")
        sets = ", ".join(f"`{k}` = %s" for k in data)
        sql = f"UPDATE `{TABLE}` SET {sets} WHERE `id` = %s"
        return self.db.execute(sql, (*data.values(), user_id))

    def update_status(self, user_id: int, status: int) -> int:
        return self.db.execute(
            f"UPDATE `{TABLE}` SET `status` = %s WHERE `id` = %s", (status, user_id)
        )

    def update_batch_status(self, ids: Sequence[int], status: int) -> int:
        """批量修改状态。"""
        if not ids:
            return 0
        placeholders = ", ".join(["%s"] * len(ids))
        return self.db.execute(
            f"UPDATE `{TABLE}` SET `status` = %s WHERE `id` IN ({placeholders})",
            (status, *ids),
        )

    # ================================================================ 查
    def get_by_id(self, user_id: int) -> Optional[User]:
        row = self.db.query_one(f"SELECT * FROM `{TABLE}` WHERE `id` = %s", (user_id,))
        return User.from_row(row) if row else None

    def get_by_username(self, username: str) -> Optional[User]:
        row = self.db.query_one(
            f"SELECT * FROM `{TABLE}` WHERE `username` = %s", (username,)
        )
        return User.from_row(row) if row else None

    def list_all(self, limit: int = 100, offset: int = 0) -> list[User]:
        rows = self.db.query_all(
            f"SELECT * FROM `{TABLE}` ORDER BY `id` LIMIT %s OFFSET %s", (limit, offset)
        )
        return [User.from_row(r) for r in rows]

    def list_by_status(self, status: int) -> list[User]:
        rows = self.db.query_all(
            f"SELECT * FROM `{TABLE}` WHERE `status` = %s ORDER BY `id`", (status,)
        )
        return [User.from_row(r) for r in rows]

    def search(
        self,
        keyword: Optional[str] = None,
        status: Optional[int] = None,
        min_age: Optional[int] = None,
        max_age: Optional[int] = None,
        order_by: str = "id",
        desc: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> list[User]:
        """条件查询：关键字模糊匹配用户名/昵称/邮箱，可叠加状态与年龄区间。"""
        where, params = ["1 = 1"], []
        if keyword:
            where.append("(`username` LIKE %s OR `nickname` LIKE %s OR `email` LIKE %s)")
            like = f"%{keyword}%"
            params.extend([like, like, like])
        if status is not None:
            where.append("`status` = %s")
            params.append(status)
        if min_age is not None:
            where.append("`age` >= %s")
            params.append(min_age)
        if max_age is not None:
            where.append("`age` <= %s")
            params.append(max_age)

        order_col = order_by if order_by in ("id", "age", "created_at", "username") else "id"
        direction = "DESC" if desc else "ASC"
        sql = (
            f"SELECT * FROM `{TABLE}` WHERE {' AND '.join(where)} "
            f"ORDER BY `{order_col}` {direction} LIMIT %s OFFSET %s"
        )
        rows = self.db.query_all(sql, (*params, limit, offset))
        return [User.from_row(r) for r in rows]

    def count(self, status: Optional[int] = None) -> int:
        if status is None:
            return int(self.db.scalar(f"SELECT COUNT(*) AS c FROM `{TABLE}`") or 0)
        return int(
            self.db.scalar(
                f"SELECT COUNT(*) AS c FROM `{TABLE}` WHERE `status` = %s", (status,)
            )
            or 0
        )

    def exists(self, username: str) -> bool:
        return bool(
            self.db.scalar(
                f"SELECT COUNT(*) AS c FROM `{TABLE}` WHERE `username` = %s", (username,)
            )
        )

    def group_by_gender(self) -> list[dict]:
        """聚合查询示例：按性别统计人数。"""
        return self.db.query_all(
            f"SELECT `gender`, COUNT(*) AS total FROM `{TABLE}` GROUP BY `gender` "
            "ORDER BY `gender`"
        )

    # ================================================================ 事务示例
    def swap_status_in_tx(self, from_id: int, to_id: int) -> None:
        """在同一事务中把 from_id 置为禁用、to_id 置为启用。

        演示原子性：任一步校验失败都会抛异常，事务整体回滚。
        注意：不能依赖 UPDATE 的 rowcount 判断记录是否存在——
        当新值与旧值相同时 MySQL 也会返回 0，因此这里显式 SELECT 校验。
        """
        with self.db.transaction() as cur:
            cur.execute(
                f"SELECT `id` FROM `{TABLE}` WHERE `id` IN (%s, %s)", (from_id, to_id)
            )
            found = {row["id"] for row in cur.fetchall()}
            missing = {from_id, to_id} - found
            if missing:
                raise ValueError(f"用户不存在：{sorted(missing)}")
            cur.execute(f"UPDATE `{TABLE}` SET `status` = 0 WHERE `id` = %s", (from_id,))
            cur.execute(f"UPDATE `{TABLE}` SET `status` = 1 WHERE `id` = %s", (to_id,))
