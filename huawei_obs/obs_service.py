"""Thin, opinionated wrapper around the Huawei Cloud OBS Python SDK.

Covers the common object-storage operations: bucket listing/creation, object
upload, download, listing, metadata lookup, copy, deletion and temporary
(pre-signed) URL generation.

The wrapper normalises the SDK's response objects into plain Python values and
raises :class:`ObsError` on any non-2xx response, so callers do not have to
check ``result.status`` everywhere.
"""

from __future__ import annotations

import mimetypes
import os
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from obs import ObsClient
from obs import DeleteObjectsRequest, Object as ObsObject
from obs.model import UploadFileHeader

from .config import ObsConfig


class ObsError(RuntimeError):
    """Raised when an OBS request returns a non-success status."""

    def __init__(self, operation: str, status: Optional[int], reason: str,
                 error_code: Optional[str] = None, error_message: Optional[str] = None,
                 request_id: Optional[str] = None):
        self.operation = operation
        self.status = status
        self.reason = reason
        self.error_code = error_code
        self.error_message = error_message
        self.request_id = request_id
        detail = error_message or reason or "unknown error"
        super().__init__(f"{operation} failed (status={status}): {detail}")


@dataclass
class ObjectInfo:
    """Normalised description of a stored object."""

    key: str
    size: int = 0
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    content_type: Optional[str] = None
    storage_class: Optional[str] = None

    @classmethod
    def from_content(cls, content) -> "ObjectInfo":
        return cls(
            key=content.key,
            size=int(content.size or 0),
            etag=getattr(content, "etag", None),
            last_modified=getattr(content, "lastModified", None),
            storage_class=getattr(content, "storageClass", None),
        )


@dataclass
class ListResult:
    """One page of a bucket listing."""

    objects: list[ObjectInfo] = field(default_factory=list)
    prefixes: list[str] = field(default_factory=list)
    is_truncated: bool = False
    next_marker: Optional[str] = None


def _is_success(status: Optional[int]) -> bool:
    return status is not None and 200 <= status < 300


class ObsService:
    """High level OBS client bound to a single configuration and bucket."""

    def __init__(self, config: ObsConfig):
        config.validate()
        self.config = config
        self.bucket = config.bucket
        self._client = ObsClient(
            access_key_id=config.access_key_id,
            secret_access_key=config.secret_access_key,
            security_token=config.security_token,
            server=config.endpoint,
            signature=config.signature,
            region=config.region,
            path_style=config.path_style,
            ssl_verify=config.ssl_verify,
            timeout=config.timeout,
            max_retry_count=config.max_retry_count,
        )

    # ------------------------------------------------------------------ helpers
    def _check(self, operation: str, result):
        if not _is_success(getattr(result, "status", None)):
            raise ObsError(
                operation,
                getattr(result, "status", None),
                getattr(result, "reason", None),
                getattr(result, "errorCode", None),
                getattr(result, "errorMessage", None),
                getattr(result, "requestId", None),
            )
        return result

    def _bucket(self, bucket: Optional[str]) -> str:
        name = bucket or self.bucket
        if not name:
            raise ValueError("no bucket specified and no default bucket configured")
        return name

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ObsService":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ buckets
    def list_buckets(self) -> list[str]:
        """Return the names of all buckets visible to the credentials."""
        result = self._check("listBuckets", self._client.listBuckets())
        buckets = getattr(result.body, "buckets", None) or []
        return [b.name for b in buckets]

    def bucket_exists(self, bucket: Optional[str] = None) -> bool:
        result = self._client.headBucket(self._bucket(bucket))
        if getattr(result, "status", None) == 404:
            return False
        self._check("headBucket", result)
        return True

    def create_bucket(self, bucket: Optional[str] = None,
                      location: Optional[str] = None) -> bool:
        """Create a bucket. Returns True when created, False when it already exists."""
        name = self._bucket(bucket)
        result = self._client.createBucket(name, location=location)
        if getattr(result, "status", None) == 409:
            return False
        self._check("createBucket", result)
        return True

    def delete_bucket(self, bucket: Optional[str] = None) -> None:
        self._check("deleteBucket", self._client.deleteBucket(self._bucket(bucket)))

    def get_bucket_location(self, bucket: Optional[str] = None) -> Optional[str]:
        result = self._check("getBucketMetadata",
                             self._client.getBucketMetadata(self._bucket(bucket)))
        return getattr(result.body, "location", None)

    # ------------------------------------------------------------------ upload
    def upload_bytes(self, key: str, data: bytes,
                     content_type: Optional[str] = None,
                     metadata: Optional[dict] = None,
                     bucket: Optional[str] = None) -> ObjectInfo:
        """Upload an in-memory payload under ``key``."""
        if isinstance(data, str):
            data = data.encode("utf-8")
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type
        result = self._check(
            "putContent",
            self._client.putContent(self._bucket(bucket), key, content=data,
                                    metadata=metadata, headers=headers or None),
        )
        return ObjectInfo(
            key=key,
            size=len(data),
            etag=getattr(result.body, "etag", None),
        )

    def upload_file(self, local_path: str, key: Optional[str] = None,
                    content_type: Optional[str] = None,
                    metadata: Optional[dict] = None,
                    progress_callback: Optional[Callable] = None,
                    bucket: Optional[str] = None) -> ObjectInfo:
        """Upload a local file, using the SDK's resumable multipart upload."""
        if not os.path.isfile(local_path):
            raise FileNotFoundError(local_path)
        object_key = key or os.path.basename(local_path)
        ctype = content_type or mimetypes.guess_type(local_path)[0]
        upload_headers = UploadFileHeader(contentType=ctype) if ctype else None
        result = self._check(
            "uploadFile",
            self._client.uploadFile(self._bucket(bucket), object_key, local_path,
                                    metadata=metadata, headers=upload_headers,
                                    progressCallback=progress_callback),
        )
        return ObjectInfo(
            key=object_key,
            size=os.path.getsize(local_path),
            etag=getattr(result.body, "etag", None),
        )

    # ------------------------------------------------------------------ download
    def download_file(self, key: str, local_path: str,
                      progress_callback: Optional[Callable] = None,
                      bucket: Optional[str] = None) -> str:
        """Download ``key`` to ``local_path`` (resumable). Returns the local path."""
        parent = os.path.dirname(os.path.abspath(local_path))
        os.makedirs(parent, exist_ok=True)
        self._check(
            "downloadFile",
            self._client.downloadFile(self._bucket(bucket), key, downloadFile=local_path,
                                      progressCallback=progress_callback),
        )
        return local_path

    def get_object_bytes(self, key: str, bucket: Optional[str] = None) -> bytes:
        """Fetch an object entirely into memory."""
        result = self._check(
            "getObject",
            self._client.getObject(self._bucket(bucket), key, loadStreamInMemory=True),
        )
        return result.body.buffer

    def get_object_stream(self, key: str, bucket: Optional[str] = None):
        """Return the raw streaming response body (``.response.read()``)."""
        result = self._check("getObject", self._client.getObject(self._bucket(bucket), key))
        return result.body

    # ------------------------------------------------------------------ inspect
    def stat_object(self, key: str, bucket: Optional[str] = None) -> ObjectInfo:
        """Return metadata for a single object."""
        result = self._check(
            "getObjectMetadata",
            self._client.getObjectMetadata(self._bucket(bucket), key),
        )
        body = result.body
        return ObjectInfo(
            key=key,
            size=int(getattr(body, "contentLength", 0) or 0),
            etag=getattr(body, "etag", None),
            last_modified=getattr(body, "lastModified", None),
            content_type=getattr(body, "contentType", None),
        )

    def object_exists(self, key: str, bucket: Optional[str] = None) -> bool:
        result = self._client.getObjectMetadata(self._bucket(bucket), key)
        if getattr(result, "status", None) == 404:
            return False
        self._check("getObjectMetadata", result)
        return True

    # ------------------------------------------------------------------ listing
    def list_objects(self, prefix: Optional[str] = None, delimiter: Optional[str] = None,
                     max_keys: Optional[int] = None, marker: Optional[str] = None,
                     bucket: Optional[str] = None) -> ListResult:
        """List one page of objects."""
        result = self._check(
            "listObjects",
            self._client.listObjects(self._bucket(bucket), prefix=prefix, delimiter=delimiter,
                                     max_keys=max_keys, marker=marker),
        )
        body = result.body
        raw_prefixes = getattr(body, "commonPrefixs", None) or getattr(body, "commonPrefixes", None) or []
        return ListResult(
            objects=[ObjectInfo.from_content(c) for c in (getattr(body, "contents", None) or [])],
            prefixes=[getattr(p, "prefix", str(p)) for p in raw_prefixes],
            is_truncated=bool(getattr(body, "isTruncated", False)),
            next_marker=getattr(body, "nextMarker", None),
        )

    def list_all_objects(self, prefix: Optional[str] = None,
                         bucket: Optional[str] = None) -> list[ObjectInfo]:
        """List every object under ``prefix``, transparently following pagination."""
        collected: list[ObjectInfo] = []
        marker: Optional[str] = None
        while True:
            page = self.list_objects(prefix=prefix, marker=marker, bucket=bucket)
            collected.extend(page.objects)
            if not page.is_truncated or not page.next_marker:
                break
            marker = page.next_marker
        return collected

    def list_prefixes(self, prefix: str = "", bucket: Optional[str] = None) -> list[str]:
        """Return the "directories" directly under ``prefix``."""
        page = self.list_objects(prefix=prefix, delimiter="/", bucket=bucket)
        return page.prefixes

    # ------------------------------------------------------------------ delete
    def delete_object(self, key: str, bucket: Optional[str] = None) -> None:
        self._check("deleteObject", self._client.deleteObject(self._bucket(bucket), key))

    def delete_objects(self, keys: Iterable[str], bucket: Optional[str] = None) -> list[str]:
        """Delete many objects in a single request. Returns the deleted keys."""
        key_list = list(keys)
        if not key_list:
            return []
        request = DeleteObjectsRequest(
            quiet=False, objects=[ObsObject(key=k) for k in key_list]
        )
        result = self._check(
            "deleteObjects",
            self._client.deleteObjects(self._bucket(bucket), request),
        )
        deleted = getattr(result.body, "deleted", None) or []
        return [getattr(d, "key", str(d)) for d in deleted]

    # ------------------------------------------------------------------ copy
    def copy_object(self, source_key: str, dest_key: str,
                    source_bucket: Optional[str] = None,
                    dest_bucket: Optional[str] = None,
                    metadata: Optional[dict] = None) -> None:
        self._check(
            "copyObject",
            self._client.copyObject(self._bucket(source_bucket), source_key,
                                    self._bucket(dest_bucket), dest_key, metadata=metadata),
        )

    # ------------------------------------------------------------------ signed URLs
    def generate_signed_url(self, key: str, method: str = "GET", expires: int = 3600,
                            headers: Optional[dict] = None,
                            query_params: Optional[dict] = None,
                            bucket: Optional[str] = None) -> str:
        """Create a temporary pre-signed URL for an object.

        ``method`` is any HTTP verb (GET to download, PUT to upload, DELETE to remove).
        ``expires`` is the lifetime in seconds.
        """
        result = self._client.createSignedUrl(
            method, bucketName=self._bucket(bucket), objectKey=key,
            expires=expires, headers=headers, queryParams=query_params,
        )
        signed = getattr(result, "signedUrl", None)
        if not signed:
            raise ObsError("createSignedUrl", getattr(result, "status", None),
                           "no signedUrl returned")
        return signed

    def generate_upload_url(self, key: str, expires: int = 3600,
                            bucket: Optional[str] = None) -> str:
        """Pre-signed URL that lets a client PUT an object without credentials."""
        return self.generate_signed_url(key, method="PUT", expires=expires, bucket=bucket)
