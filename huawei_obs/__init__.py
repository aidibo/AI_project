"""华为云 OBS Python 操作示例包。

基于官方 SDK ``esdk-obs-python``，覆盖查看、上传、下载、列表、临时访问地址等常用操作。
"""

from .client import ListResult, ObjectInfo, ObsError, ObsService
from .config import ObsConfig

__all__ = ["ObsConfig", "ObsService", "ObsError", "ObjectInfo", "ListResult"]
