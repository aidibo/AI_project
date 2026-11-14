"""OBS 操作层：对华为云官方 SDK（``esdk-obs-python``）的轻量封装。

封装目标：

* 把 SDK 返回的响应对象归一化成普通的 Python 值（``ObjectInfo`` / ``ListResult``）；
* 非 2xx 响应统一抛出 :class:`ObsError`，调用方无需到处判断 ``result.status``；
* 覆盖桶查看、对象上传、下载、列表、元数据、复制、删除、临时访问地址等常用操作。

所有方法都直接调用真实 SDK，不含任何 mock。
"""

from __future__ import annotations

import mimetypes
import os
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, Optional

from obs import DeleteObjectsRequest, ObsClient as _SdkClient
from obs import Object as _SdkObject
from obs.model import UploadFileHeader

from .config import ObsConfig

# 预签名 URL 支持的 HTTP 方法
_SIGNED_METHODS = ("GET", "PUT", "DELETE", "HEAD", "POST")


class ObsError(RuntimeError):
    """OBS 请求返回非成功状态时抛出。

    保留服务端返回的错误码、错误信息与请求 ID，便于排查问题。
    """

    def __init__(
        self,
        operation: str,
        status: Optional[int] = None,
        reason: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> None:
        self.operation = operation
        self.status = status
        self.reason = reason
        self.error_code = error_code
        self.error_message = error_message
        self.request_id = request_id
        detail = error_message or reason or "未知错误"
        super().__init__(f"{operation} 失败（HTTP {status}）：{detail}")


@dataclass
class ObjectInfo:
    """归一化后的对象描述。"""

    key: str
    size: int = 0
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    content_type: Optional[str] = None
    storage_class: Optional[str] = None

    @classmethod
    def from_list_entry(cls, entry) -> "ObjectInfo":
        return cls(
            key=entry.key,
            size=int(entry.size or 0),
            etag=getattr(entry, "etag", None),
            last_modified=getattr(entry, "lastModified", None),
            storage_class=getattr(entry, "storageClass", None),
        )


@dataclass
class ListResult:
    """对象列表的一页结果。"""

    objects: list[ObjectInfo] = field(default_factory=list)
    prefixes: list[str] = field(default_factory=list)
    is_truncated: bool = False
    next_marker: Optional[str] = None


def _is_success(status: Optional[int]) -> bool:
    return status is not None and 200 <= status < 300


def _first_attr(obj, *names, default=None):
    """返回对象上第一个存在的属性值。

    SDK 不同版本 / 不同响应字段命名风格不一致（有的用 snake_case，有的用
    camelCase），这里统一兼容。
    """
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return default


class ObsService:
    """绑定到单个配置与默认桶的高层 OBS 客户端。

    用法::

        with ObsService(ObsConfig.from_env()) as obs:
            obs.upload_bytes("docs/a.txt", "hello")
            print(obs.stat_object("docs/a.txt").size)
    """

    def __init__(self, config: ObsConfig) -> None:
        config.validate()
        self.config = config
        self.bucket = config.bucket
        self._client = _SdkClient(
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

    # ------------------------------------------------------------ 内部工具
    def _check(self, operation: str, result):
        """校验响应状态，非 2xx 抛出 :class:`ObsError`。"""
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

    def _resolve_bucket(self, bucket: Optional[str]) -> str:
        name = bucket or self.bucket
        if not name:
            raise ValueError("未指定桶名，且配置中也没有默认桶（OBS_BUCKET）")
        return name

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ObsService":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ------------------------------------------------------------ 桶操作
    def list_buckets(self) -> list[str]:
        """列出当前凭证可见的所有桶名。"""
        result = self._check("listBuckets", self._client.listBuckets())
        buckets = getattr(result.body, "buckets", None) or []
        return [b.name for b in buckets]

    def bucket_exists(self, bucket: Optional[str] = None) -> bool:
        """判断桶是否存在（HEAD 请求，404 视为不存在）。"""
        result = self._client.headBucket(self._resolve_bucket(bucket))
        if getattr(result, "status", None) == 404:
            return False
        self._check("headBucket", result)
        return True

    def create_bucket(self, bucket: Optional[str] = None,
                      location: Optional[str] = None) -> bool:
        """创建桶。返回 True 表示新建，False 表示已存在。"""
        name = self._resolve_bucket(bucket)
        result = self._client.createBucket(name, location=location)
        if getattr(result, "status", None) == 409:  # 已存在且归当前用户所有
            return False
        self._check("createBucket", result)
        return True

    def delete_bucket(self, bucket: Optional[str] = None) -> None:
        """删除桶（桶必须为空）。"""
        self._check("deleteBucket", self._client.deleteBucket(self._resolve_bucket(bucket)))

    def get_bucket_location(self, bucket: Optional[str] = None) -> Optional[str]:
        """返回桶所在区域。"""
        result = self._check(
            "getBucketMetadata", self._client.getBucketMetadata(self._resolve_bucket(bucket))
        )
        return getattr(result.body, "location", None)

    # ------------------------------------------------------------ 上传
    def upload_bytes(self, key: str, data,
                     content_type: Optional[str] = None,
                     metadata: Optional[dict] = None,
                     bucket: Optional[str] = None) -> ObjectInfo:
        """把内存中的 bytes / str 上传为对象。"""
        if isinstance(data, str):
            data = data.encode("utf-8")
        headers = {"Content-Type": content_type} if content_type else None
        result = self._check(
            "putContent",
            self._client.putContent(self._resolve_bucket(bucket), key, content=data,
                                    metadata=metadata, headers=headers),
        )
        return ObjectInfo(key=key, size=len(data),
                          etag=getattr(result.body, "etag", None))

    def upload_file(self, local_path: str, key: Optional[str] = None,
                    content_type: Optional[str] = None,
                    metadata: Optional[dict] = None,
                    progress_callback: Optional[Callable] = None,
                    bucket: Optional[str] = None) -> ObjectInfo:
        """上传本地文件，使用 SDK 的分片 / 断点续传能力。"""
        if not os.path.isfile(local_path):
            raise FileNotFoundError(local_path)
        object_key = key or os.path.basename(local_path)
        guessed = content_type or mimetypes.guess_type(local_path)[0]
        headers = UploadFileHeader(contentType=guessed) if guessed else None
        result = self._check(
            "uploadFile",
            self._client.uploadFile(self._resolve_bucket(bucket), object_key, local_path,
                                    metadata=metadata, headers=headers,
                                    progressCallback=progress_callback),
        )
        return ObjectInfo(key=object_key, size=os.path.getsize(local_path),
                          etag=getattr(result.body, "etag", None))

    # ------------------------------------------------------------ 下载
    def download_file(self, key: str, local_path: str,
                      progress_callback: Optional[Callable] = None,
                      bucket: Optional[str] = None) -> str:
        """下载对象到本地文件（自动建目录），返回本地路径。"""
        os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)
        self._check(
            "downloadFile",
            self._client.downloadFile(self._resolve_bucket(bucket), key,
                                      downloadFile=local_path,
                                      progressCallback=progress_callback),
        )
        return local_path

    def get_object_bytes(self, key: str, bucket: Optional[str] = None) -> bytes:
        """把对象完整读入内存并返回 bytes。"""
        result = self._check(
            "getObject",
            self._client.getObject(self._resolve_bucket(bucket), key,
                                   loadStreamInMemory=True),
        )
        return result.body.buffer

    def get_object_text(self, key: str, encoding: str = "utf-8",
                        bucket: Optional[str] = None) -> str:
        """把对象读入内存并按指定编码解码为字符串。"""
        return self.get_object_bytes(key, bucket=bucket).decode(encoding)

    def get_object_stream(self, key: str, bucket: Optional[str] = None):
        """返回流式响应体，读取方式为 ``stream.response.read()``。"""
        result = self._check(
            "getObject", self._client.getObject(self._resolve_bucket(bucket), key)
        )
        return result.body

    # ------------------------------------------------------------ 查看 / 元数据
    def stat_object(self, key: str, bucket: Optional[str] = None) -> ObjectInfo:
        """返回单个对象的元数据。"""
        result = self._check(
            "getObjectMetadata",
            self._client.getObjectMetadata(self._resolve_bucket(bucket), key),
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
        """判断对象是否存在。"""
        result = self._client.getObjectMetadata(self._resolve_bucket(bucket), key)
        if getattr(result, "status", None) == 404:
            return False
        self._check("getObjectMetadata", result)
        return True

    def set_object_metadata(self, key: str, metadata: dict,
                            content_type: Optional[str] = None,
                            bucket: Optional[str] = None) -> None:
        """更新对象自定义元数据（会覆盖原元数据）。"""
        headers = {"Content-Type": content_type} if content_type else None
        self._check(
            "setObjectMetadata",
            self._client.setObjectMetadata(self._resolve_bucket(bucket), key,
                                           metadata=metadata, headers=headers),
        )

    def get_object_acl(self, key: str, bucket: Optional[str] = None):
        """返回对象的 ACL 信息。"""
        result = self._check(
            "getObjectAcl",
            self._client.getObjectAcl(self._resolve_bucket(bucket), key),
        )
        return result.body

    # ------------------------------------------------------------ 列表
    def list_objects(self, prefix: Optional[str] = None, delimiter: Optional[str] = None,
                     max_keys: Optional[int] = None, marker: Optional[str] = None,
                     bucket: Optional[str] = None) -> ListResult:
        """列出对象的一页结果。"""
        result = self._check(
            "listObjects",
            self._client.listObjects(self._resolve_bucket(bucket), prefix=prefix,
                                     delimiter=delimiter, max_keys=max_keys, marker=marker),
        )
        body = result.body
        # SDK 里该属性拼写为 commonPrefixs（缺少 e），兼容两种写法
        raw_prefixes = (getattr(body, "commonPrefixs", None)
                        or getattr(body, "commonPrefixes", None) or [])
        return ListResult(
            objects=[ObjectInfo.from_list_entry(c)
                     for c in (getattr(body, "contents", None) or [])],
            prefixes=[getattr(p, "prefix", str(p)) for p in raw_prefixes],
            is_truncated=bool(_first_attr(body, "is_truncated", "isTruncated", default=False)),
            next_marker=_first_attr(body, "next_marker", "nextMarker"),
        )

    def iter_objects(self, prefix: Optional[str] = None, max_keys: Optional[int] = None,
                     bucket: Optional[str] = None) -> Iterator[ObjectInfo]:
        """逐页迭代对象，适合对象数量较多的桶。"""
        marker: Optional[str] = None
        while True:
            page = self.list_objects(prefix=prefix, marker=marker, max_keys=max_keys,
                                     bucket=bucket)
            yield from page.objects
            if not page.is_truncated or not page.next_marker:
                break
            marker = page.next_marker

    def list_all_objects(self, prefix: Optional[str] = None,
                         bucket: Optional[str] = None) -> list[ObjectInfo]:
        """一次性列出前缀下所有对象（内部自动翻页）。"""
        return list(self.iter_objects(prefix=prefix, bucket=bucket))

    def list_prefixes(self, prefix: str = "", bucket: Optional[str] = None) -> list[str]:
        """列出 ``prefix`` 下的“目录”（公共前缀）。"""
        page = self.list_objects(prefix=prefix, delimiter="/", bucket=bucket)
        return page.prefixes

    # ------------------------------------------------------------ 删除
    def delete_object(self, key: str, bucket: Optional[str] = None) -> None:
        """删除单个对象。"""
        self._check("deleteObject", self._client.deleteObject(self._resolve_bucket(bucket), key))

    def delete_objects(self, keys: Iterable[str], bucket: Optional[str] = None) -> list[str]:
        """批量删除对象（单次请求）。返回实际删除的 key 列表。"""
        key_list = list(keys)
        if not key_list:
            return []
        request = DeleteObjectsRequest(quiet=False,
                                       objects=[_SdkObject(key=k) for k in key_list])
        result = self._check(
            "deleteObjects",
            self._client.deleteObjects(self._resolve_bucket(bucket), request),
        )
        deleted = getattr(result.body, "deleted", None) or []
        return [getattr(d, "key", str(d)) for d in deleted]

    def delete_prefix(self, prefix: str, bucket: Optional[str] = None) -> int:
        """删除某前缀下的全部对象，返回删除数量（按批处理）。"""
        keys = [info.key for info in self.iter_objects(prefix=prefix, bucket=bucket)]
        for start in range(0, len(keys), 1000):  # 单次最多 1000 个
            self.delete_objects(keys[start:start + 1000], bucket=bucket)
        return len(keys)

    # ------------------------------------------------------------ 复制
    def copy_object(self, source_key: str, dest_key: str,
                    source_bucket: Optional[str] = None,
                    dest_bucket: Optional[str] = None,
                    metadata: Optional[dict] = None) -> None:
        """在桶内或跨桶复制对象。"""
        self._check(
            "copyObject",
            self._client.copyObject(self._resolve_bucket(source_bucket), source_key,
                                    self._resolve_bucket(dest_bucket), dest_key,
                                    metadata=metadata),
        )

    # ------------------------------------------------------------ 临时访问地址
    def generate_signed_url(self, key: str, method: str = "GET", expires: int = 3600,
                            headers: Optional[dict] = None,
                            query_params: Optional[dict] = None,
                            bucket: Optional[str] = None) -> str:
        """生成对象的临时（预签名）访问地址。

        :param method: HTTP 方法，``GET`` 下载、``PUT`` 上传、``DELETE`` 删除
        :param expires: 有效期（秒）
        """
        method = method.upper()
        if method not in _SIGNED_METHODS:
            raise ValueError(f"method 必须是 {_SIGNED_METHODS} 之一，收到 {method!r}")
        result = self._client.createSignedUrl(
            method, bucketName=self._resolve_bucket(bucket), objectKey=key,
            expires=expires, headers=headers, queryParams=query_params,
        )
        signed = getattr(result, "signedUrl", None)
        if not signed:
            raise ObsError("createSignedUrl", getattr(result, "status", None),
                           "未返回 signedUrl")
        return signed

    def generate_download_url(self, key: str, expires: int = 3600,
                              bucket: Optional[str] = None) -> str:
        """生成下载用的临时地址（GET）。"""
        return self.generate_signed_url(key, method="GET", expires=expires, bucket=bucket)

    def generate_upload_url(self, key: str, expires: int = 3600,
                            bucket: Optional[str] = None) -> str:
        """生成上传用的临时地址（PUT），客户端无需 AK/SK 即可上传。"""
        return self.generate_signed_url(key, method="PUT", expires=expires, bucket=bucket)
