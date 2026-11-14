"""OBS 配置层：从环境变量 / .env 读取连接参数。

只负责“把配置读进来并校验”，不发起任何网络请求。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

_TRUE_VALUES = {"1", "true", "yes", "on"}


def _to_bool(raw: Optional[str], default: bool) -> bool:
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in _TRUE_VALUES


@dataclass
class ObsConfig:
    """单个 OBS（或 S3 兼容）端点的连接配置。

    :param access_key_id: 访问密钥 AK
    :param secret_access_key: 访问密钥 SK
    :param server: 终端节点，如 ``obs.cn-north-4.myhuaweicloud.com``；
        可带协议前缀（``http://`` / ``https://``），不带时由 ``is_secure`` 推导
    :param bucket: 默认桶名，多数操作可省略桶参数时使用
    :param region: 区域，如 ``cn-north-4``
    :param security_token: IAM 委托 / STS 临时凭证的 token，永久 AK/SK 留空
    :param signature: 签名算法，华为云 OBS 用 ``obs``，S3 兼容服务用 ``v4``
    :param is_secure: 是否使用 HTTPS
    :param path_style: 是否使用 path-style 寻址（自建 S3 兼容服务通常为 True）
    :param ssl_verify: 是否校验服务端 SSL 证书
    :param timeout: 单次请求超时（秒）
    :param max_retry_count: 失败自动重试次数
    :param port: 自定义端口，None 表示使用协议默认端口
    """

    access_key_id: str
    secret_access_key: str
    server: str
    bucket: str = ""
    region: str = "cn-north-4"
    security_token: Optional[str] = None
    signature: str = "obs"
    is_secure: bool = True
    path_style: bool = False
    ssl_verify: bool = True
    timeout: int = 60
    max_retry_count: int = 3
    port: Optional[int] = None

    @classmethod
    def from_env(cls, prefix: str = "OBS_", **overrides) -> "ObsConfig":
        """从环境变量构造配置。

        识别的变量（前缀 ``prefix``，默认 ``OBS_``）：
        ``ACCESS_KEY_ID``、``SECRET_ACCESS_KEY``、``SERVER``、``BUCKET``、
        ``REGION``、``SECURITY_TOKEN``、``SIGNATURE``、``IS_SECURE``、
        ``PATH_STYLE``、``SSL_VERIFY``、``TIMEOUT``、``MAX_RETRY_COUNT``、``PORT``。

        额外关键字参数可覆盖对应字段，便于测试时传入临时值。
        """

        def env(name: str, default: Optional[str] = None) -> Optional[str]:
            return os.getenv(f"{prefix}{name}", default)

        config = cls(
            access_key_id=env("ACCESS_KEY_ID", "") or "",
            secret_access_key=env("SECRET_ACCESS_KEY", "") or "",
            server=env("SERVER", "") or "",
            bucket=env("BUCKET", "") or "",
            region=env("REGION", "cn-north-4") or "cn-north-4",
            security_token=env("SECURITY_TOKEN") or None,
            signature=env("SIGNATURE", "obs") or "obs",
            is_secure=_to_bool(env("IS_SECURE"), True),
            path_style=_to_bool(env("PATH_STYLE"), False),
            ssl_verify=_to_bool(env("SSL_VERIFY"), True),
            timeout=int(env("TIMEOUT", "60") or 60),
            max_retry_count=int(env("MAX_RETRY_COUNT", "3") or 3),
            port=int(env("PORT")) if env("PORT") else None,
        )
        for name, value in overrides.items():
            if value is not None:
                setattr(config, name, value)
        return config

    @property
    def endpoint(self) -> str:
        """带协议与可选端口的完整终端节点 URL。"""
        if "://" in self.server:
            return self.server
        scheme = "https" if self.is_secure else "http"
        host = f"{self.server}:{self.port}" if self.port else self.server
        return f"{scheme}://{host}"

    def validate(self, require_bucket: bool = False) -> None:
        """校验必填项，缺失时抛出 ``ValueError``。"""
        missing = [
            name
            for name, value in (
                ("OBS_ACCESS_KEY_ID", self.access_key_id),
                ("OBS_SECRET_ACCESS_KEY", self.secret_access_key),
                ("OBS_SERVER", self.server),
            )
            if not value
        ]
        if require_bucket and not self.bucket:
            missing.append("OBS_BUCKET")
        if missing:
            raise ValueError("缺少必要的 OBS 配置项：" + "、".join(missing))
