"""OBS connection settings loaded from environment / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

_TRUE = {"1", "true", "yes", "on"}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE


@dataclass
class ObsConfig:
    """Settings for a single Huawei Cloud OBS (or S3-compatible) endpoint.

    ``server`` is the endpoint host, e.g. ``obs.cn-north-4.myhuaweicloud.com``.
    A scheme may be included; when omitted the scheme is derived from ``is_secure``.
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
        """Build a config from environment variables.

        Recognised variables (with ``prefix``): ACCESS_KEY_ID, SECRET_ACCESS_KEY,
        SERVER, BUCKET, REGION, SECURITY_TOKEN, SIGNATURE, IS_SECURE, PATH_STYLE,
        SSL_VERIFY, TIMEOUT, MAX_RETRY_COUNT, PORT.
        """
        p = prefix

        def get(name: str, default=None):
            return os.getenv(f"{p}{name}", default)

        cfg = cls(
            access_key_id=get("ACCESS_KEY_ID", ""),
            secret_access_key=get("SECRET_ACCESS_KEY", ""),
            server=get("SERVER", ""),
            bucket=get("BUCKET", ""),
            region=get("REGION", "cn-north-4"),
            security_token=get("SECURITY_TOKEN") or None,
            signature=get("SIGNATURE", "obs"),
            is_secure=_env_bool(f"{p}IS_SECURE", True),
            path_style=_env_bool(f"{p}PATH_STYLE", False),
            ssl_verify=_env_bool(f"{p}SSL_VERIFY", True),
            timeout=int(get("TIMEOUT", 60)),
            max_retry_count=int(get("MAX_RETRY_COUNT", 3)),
            port=int(get("PORT")) if get("PORT") else None,
        )
        for key, value in overrides.items():
            if value is not None:
                setattr(cfg, key, value)
        return cfg

    def validate(self, require_bucket: bool = False) -> None:
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
            raise ValueError(
                "missing required OBS configuration: " + ", ".join(missing)
            )

    @property
    def endpoint(self) -> str:
        """Full endpoint URL including scheme and optional port."""
        server = self.server
        if "://" in server:
            return server
        scheme = "https" if self.is_secure else "http"
        if self.port:
            return f"{scheme}://{server}:{self.port}"
        return f"{scheme}://{server}"
