"""数据库连接层：连接池、游标上下文、事务上下文。

只依赖 PyMySQL，不引入 ORM，便于理解底层 SQL 的执行过程。
"""

from __future__ import annotations

import logging
import queue
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Optional, Sequence

import pymysql
from pymysql.cursors import DictCursor

from .config import DbConfig

logger = logging.getLogger(__name__)


class Database:
    """轻量级连接池 + 事务封装。

    用法::

        db = Database(DbConfig.from_env())
        with db.cursor() as cur:
            cur.execute("SELECT 1")
        with db.transaction() as cur:
            cur.execute("INSERT ...")
    """

    def __init__(self, config: DbConfig, pool_size: int = 5) -> None:
        self.config = config
        self.pool_size = pool_size
        self._pool: "queue.LifoQueue[pymysql.connections.Connection]" = queue.LifoQueue(
            maxsize=pool_size
        )

    # ---------------------------------------------------------------- 连接
    def _new_connection(self) -> pymysql.connections.Connection:
        return pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            database=self.config.database,
            charset=self.config.charset,
            autocommit=False,
            cursorclass=DictCursor,
            connect_timeout=10,
            read_timeout=30,
            write_timeout=30,
        )

    def _acquire(self) -> pymysql.connections.Connection:
        try:
            conn = self._pool.get_nowait()
        except queue.Empty:
            conn = self._new_connection()
        else:
            # 归还的连接可能已被服务端断开（wait_timeout），探测失败则重建
            try:
                conn.ping(reconnect=False)
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = self._new_connection()
        return conn

    def _release(self, conn: pymysql.connections.Connection) -> None:
        try:
            self._pool.put_nowait(conn)
        except queue.Full:
            conn.close()

    # ---------------------------------------------------------------- 上下文
    @contextmanager
    def cursor(self, commit: bool = False) -> Iterator[DictCursor]:
        """获取一个游标；commit=True 时自动提交，异常时回滚。"""
        conn = self._acquire()
        cur = conn.cursor()
        try:
            yield cur
            if commit:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            self._release(conn)

    @contextmanager
    def transaction(self) -> Iterator[DictCursor]:
        """在一个事务中执行多条 SQL，全部成功才提交。"""
        conn = self._acquire()
        cur = conn.cursor()
        try:
            conn.begin()
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            self._release(conn)

    # ---------------------------------------------------------------- 便捷方法
    def execute(
        self, sql: str, params: Optional[Sequence[Any] | dict] = None
    ) -> int:
        """执行写操作，返回受影响行数。"""
        with self.cursor(commit=True) as cur:
            return cur.execute(sql, params)

    def executemany(self, sql: str, seq_params: Iterable[Sequence[Any]]) -> int:
        """批量执行写操作，返回受影响行数。"""
        with self.cursor(commit=True) as cur:
            return cur.executemany(sql, list(seq_params))

    def query_one(
        self, sql: str, params: Optional[Sequence[Any] | dict] = None
    ) -> Optional[dict]:
        with self.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def query_all(
        self, sql: str, params: Optional[Sequence[Any] | dict] = None
    ) -> list[dict]:
        with self.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())

    def scalar(self, sql: str, params: Optional[Sequence[Any] | dict] = None) -> Any:
        with self.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            if not row:
                return None
            return next(iter(row.values()))

    # ---------------------------------------------------------------- 生命周期
    def close(self) -> None:
        while True:
            try:
                conn = self._pool.get_nowait()
            except queue.Empty:
                break
            try:
                conn.close()
            except Exception:  # pragma: no cover - 关闭失败无需处理
                pass

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
