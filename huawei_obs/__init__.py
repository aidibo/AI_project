"""Huawei Cloud OBS access helpers built on the official ``esdk-obs-python`` SDK."""

from .config import ObsConfig
from .obs_service import ObsError, ObsService, ObjectInfo, ListResult

__all__ = ["ObsConfig", "ObsService", "ObsError", "ObjectInfo", "ListResult"]
