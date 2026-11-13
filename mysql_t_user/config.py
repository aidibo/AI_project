"""数据库配置：优先读取环境变量，其次读取 .env 文件。"""

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv 可选
    pass


@dataclass(frozen=True)
class DbConfig:
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = "demo_db"
    charset: str = "utf8mb4"

    @classmethod
    def from_env(cls) -> "DbConfig":
        return cls(
            host=os.getenv("DB_HOST", cls.host),
            port=int(os.getenv("DB_PORT", str(cls.port))),
            user=os.getenv("DB_USER", cls.user),
            password=os.getenv("DB_PASSWORD", cls.password),
            database=os.getenv("DB_NAME", cls.database),
            charset=os.getenv("DB_CHARSET", cls.charset),
        )

    def as_pymysql_kwargs(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "database": self.database,
            "charset": self.charset,
            "autocommit": False,
            "cursorclass": None,  # 运行时由 db.py 注入 DictCursor
        }
