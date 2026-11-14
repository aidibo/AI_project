"""OBS 配置层：从环境变量 / .env 读取连接参数。

职责单一——只负责“把配置读进来并校验”，不发起任何网络请求，
方便单独测试，也让连接参数集中在一处维护。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

# 导入本模块时自动加载项目根目录的 .env，使 os.getenv 能读到文件中的配置。
load_dotenv()

# 视为“真”的字符串取值（用于解析布尔型环境变量）。
_TRUE_VALUES = {"1", "true", "yes", "on"}


def _to_bool(raw: Optional[str], default: bool) -> bool:
    """把环境变量字符串解析为布尔值。

    未设置或为空字符串时返回 ``default``；否则按 :data:`_TRUE_VALUES` 判断，
    这样 ``OBS_PATH_STYLE=true``、``=1``、``=yes`` 都能正确识别。
    """
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

        :param prefix: 环境变量前缀，便于同一环境配置多套 OBS 连接
        :param overrides: 需要覆盖的字段，值为 ``None`` 时忽略；
            便于测试或调用方传入临时值而无需改动环境变量
        """

        # 读取时统一加上前缀，省去逐处拼接字符串。
        def env(name: str, default: Optional[str] = None) -> Optional[str]:
            return os.getenv(f"{prefix}{name}", default)

        config = cls(
            # 必填项：缺失时留空字符串，交给 validate() 统一报错。
            access_key_id=env("ACCESS_KEY_ID", "") or "",
            secret_access_key=env("SECRET_ACCESS_KEY", "") or "",
            server=env("SERVER", "") or "",
            bucket=env("BUCKET", "") or "",
            region=env("REGION", "cn-north-4") or "cn-north-4",
            # 临时凭证 token：空字符串一律转成 None，避免误传给 SDK。
            security_token=env("SECURITY_TOKEN") or None,
            signature=env("SIGNATURE", "obs") or "obs",
            is_secure=_to_bool(env("IS_SECURE"), True),
            path_style=_to_bool(env("PATH_STYLE"), False),
            ssl_verify=_to_bool(env("SSL_VERIFY"), True),
            # 数字型变量：空字符串时回退到默认值，避免 int("") 报错。
            timeout=int(env("TIMEOUT", "60") or 60),
            max_retry_count=int(env("MAX_RETRY_COUNT", "3") or 3),
            port=int(env("PORT")) if env("PORT") else None,
        )
        # 应用显式覆盖值（值为 None 表示“不覆盖”）。
        for name, value in overrides.items():
            if value is not None:
                setattr(config, name, value)
        return config

    @property
    def endpoint(self) -> str:
        """带协议与可选端口的完整终端节点 URL。

        若 ``server`` 已含协议前缀则原样返回；否则按 ``is_secure`` 补
        ``https://`` 或 ``http://``，并在设置了 ``port`` 时拼上端口。
        """
        if "://" in self.server:
            return self.server
        scheme = "https" if self.is_secure else "http"
        host = f"{self.server}:{self.port}" if self.port else self.server
        return f"{scheme}://{host}"

    def validate(self, require_bucket: bool = False) -> None:
        """校验必填项，缺失时抛出 ``ValueError``。

        :param require_bucket: 为 True 时把桶名也视为必填
            （只有 ``list-buckets`` 这类不需要桶的操作才可传 False）
        """
        # 收集所有缺失的必填项，一次性报全，避免用户反复试错。
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
