"""OBS 操作层：对华为云官方 SDK（``esdk-obs-python``）的轻量封装。

封装目标：

* 把 SDK 返回的响应对象归一化成普通的 Python 值（``ObjectInfo`` / ``ListResult``），
  调用方拿到的就是 dict / list / 基本类型，不必再去摸 SDK 内部结构；
* 非 2xx 响应统一抛出 :class:`ObsError`，调用方无需到处判断 ``result.status``；
* 覆盖桶查看、对象上传、下载、列表、元数据、复制、删除、临时访问地址等常用操作。

所有方法都直接调用真实 SDK，不含任何 mock。

本文件按功能分区，自上而下依次为：
内部工具 → 桶操作 → 上传 → 下载 → 查看/元数据 → 列表 → 删除 → 复制 → 临时访问地址。
"""

from __future__ import annotations

import mimetypes
import os
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, Optional

# 官方 SDK 的导入名统一是 obs；这里给关键符号加 _Sdk 前缀，
# 避免与本地类型（如 ObjectInfo）混淆。
from obs import DeleteObjectsRequest, ObsClient as _SdkClient
from obs import Object as _SdkObject
from obs.model import UploadFileHeader

from .config import ObsConfig

# 预签名 URL 支持的 HTTP 方法：GET 下载、PUT 上传、DELETE 删除、HEAD 探测、POST 提交。
_SIGNED_METHODS = ("GET", "PUT", "DELETE", "HEAD", "POST")

# 批量删除单次请求的对象数量上限（OBS/S3 协议限制为 1000）。
_MAX_DELETE_BATCH = 1000


class ObsError(RuntimeError):
    """OBS 请求返回非成功状态时抛出。

    除错误信息外，还保留服务端返回的 HTTP 状态码、错误码与请求 ID，
    便于把问题定位到具体的一次请求（``request_id`` 可直接提供给华为云工单）。
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
        self.operation = operation          # 出错的操作名，如 "getObject"
        self.status = status                # HTTP 状态码
        self.reason = reason                # HTTP 原因短语
        self.error_code = error_code        # OBS 错误码，如 "NoSuchKey"
        self.error_message = error_message  # 面向人的错误描述
        self.request_id = request_id        # 服务端请求 ID
        # 依次回退，尽量给出一句可读的错误原因。
        detail = error_message or reason or "未知错误"
        super().__init__(f"{operation} 失败（HTTP {status}）：{detail}")


@dataclass
class ObjectInfo:
    """归一化后的对象描述（列表项与元数据共用）。

    ``last_modified`` 保持服务端返回的字符串原样（不同服务格式可能不同），
    不做解析，避免因格式差异（如 ``2026/11/13 17:39:23``）导致失败。
    """

    key: str
    size: int = 0
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    content_type: Optional[str] = None
    storage_class: Optional[str] = None

    @classmethod
    def from_list_entry(cls, entry) -> "ObjectInfo":
        """由 ``listObjects`` 返回的单个条目构造。

        列表条目的字段是 camelCase（``lastModified`` / ``storageClass``），
        与列表级别的 snake_case 字段不同，这里统一转换。
        """
        return cls(
            key=entry.key,
            size=int(entry.size or 0),  # size 可能为 None，统一成 int
            etag=getattr(entry, "etag", None),
            last_modified=getattr(entry, "lastModified", None),
            storage_class=getattr(entry, "storageClass", None),
        )


@dataclass
class ListResult:
    """对象列表的一页结果。

    ``is_truncated`` 为 True 时表示还有下一页，可用 ``next_marker`` 继续拉取
    （``iter_objects`` 已封装了这一逻辑）。
    """

    objects: list[ObjectInfo] = field(default_factory=list)
    prefixes: list[str] = field(default_factory=list)
    is_truncated: bool = False
    next_marker: Optional[str] = None


def _is_success(status: Optional[int]) -> bool:
    """判断 HTTP 状态码是否属于成功区间（2xx）。"""
    return status is not None and 200 <= status < 300


def _first_attr(obj, *names, default=None):
    """返回对象上第一个“有值”的属性。

    SDK 不同接口 / 版本的字段命名风格不一致（有的用 snake_case，如
    ``is_truncated``；有的用 camelCase，如 ``isTruncated``）。这里按传入顺序
    依次尝试，取第一个非 None 的值，从而兼容多种命名。
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

    该类是线程安全的“无状态门面”：除持有 SDK 客户端外不保存可变状态，
    多线程共享同一实例即可（SDK 内部自行管理连接）。
    """

    def __init__(self, config: ObsConfig) -> None:
        # 先校验配置，尽早暴露缺失项，而不是等到第一次请求才报错。
        config.validate()
        self.config = config
        self.bucket = config.bucket          # 默认桶，可被各方法的 bucket 参数覆盖
        # 底层 SDK 客户端：把配置字段逐一映射到 SDK 参数。
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

    # ================================================================ 内部工具
    def _check(self, operation: str, result):
        """校验响应状态；非 2xx 时抛出 :class:`ObsError`，否则原样返回响应。

        把“判断状态码”收敛到这一个地方，各业务方法就只需关注成功路径。
        """
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
        """确定本次操作使用的桶：优先显式传入，其次默认桶，都没有则报错。"""
        name = bucket or self.bucket
        if not name:
            raise ValueError("未指定桶名，且配置中也没有默认桶（OBS_BUCKET）")
        return name

    def close(self) -> None:
        """关闭底层客户端，释放连接。"""
        self._client.close()

    # 支持 ``with ObsService(...) as obs:`` 用法，退出时自动关闭。
    def __enter__(self) -> "ObsService":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ================================================================ 桶操作
    def list_buckets(self) -> list[str]:
        """列出当前凭证可见的所有桶名。"""
        result = self._check("listBuckets", self._client.listBuckets())
        buckets = getattr(result.body, "buckets", None) or []
        return [b.name for b in buckets]

    def bucket_exists(self, bucket: Optional[str] = None) -> bool:
        """判断桶是否存在（HEAD 请求；404 表示不存在，其余非 2xx 视为错误）。"""
        result = self._client.headBucket(self._resolve_bucket(bucket))
        if getattr(result, "status", None) == 404:
            return False
        self._check("headBucket", result)
        return True

    def create_bucket(self, bucket: Optional[str] = None,
                      location: Optional[str] = None) -> bool:
        """创建桶。返回 True 表示新建成功，False 表示桶已存在。

        :param location: 桶所在区域，通常可省略（使用客户端默认区域）
        """
        name = self._resolve_bucket(bucket)
        result = self._client.createBucket(name, location=location)
        if getattr(result, "status", None) == 409:
            # 409 表示桶已存在且归当前账号所有——视为幂等成功。
            return False
        self._check("createBucket", result)
        return True

    def delete_bucket(self, bucket: Optional[str] = None) -> None:
        """删除桶（桶内必须为空，否则服务端报错）。"""
        self._check("deleteBucket", self._client.deleteBucket(self._resolve_bucket(bucket)))

    def get_bucket_location(self, bucket: Optional[str] = None) -> Optional[str]:
        """返回桶所在区域，如 ``cn-north-4``。"""
        result = self._check(
            "getBucketMetadata", self._client.getBucketMetadata(self._resolve_bucket(bucket))
        )
        return getattr(result.body, "location", None)

    # ================================================================ 上传
    def upload_bytes(self, key: str, data,
                     content_type: Optional[str] = None,
                     metadata: Optional[dict] = None,
                     bucket: Optional[str] = None) -> ObjectInfo:
        """把内存中的 bytes / str 上传为对象。

        :param data: ``bytes`` 或 ``str``；``str`` 会按 UTF-8 编码
        :param content_type: 内容类型，如 ``text/plain``
        :param metadata: 自定义元数据（键需以 ``x-`` 开头，由服务端校验）
        """
        # 统一转成 bytes，便于后续计算长度并交给 SDK。
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
        """上传本地文件，使用 SDK 的分片 / 断点续传能力（适合大文件）。

        :param key: 对象名；省略时取本地文件名
        :param progress_callback: 进度回调，签名见 SDK 文档
        """
        if not os.path.isfile(local_path):
            raise FileNotFoundError(local_path)
        object_key = key or os.path.basename(local_path)
        # 未显式指定时按扩展名推断内容类型，推断不出则交由服务端决定。
        guessed = content_type or mimetypes.guess_type(local_path)[0]
        # uploadFile 要求 headers 是 UploadFileHeader 对象，不能直接传 dict。
        headers = UploadFileHeader(contentType=guessed) if guessed else None
        result = self._check(
            "uploadFile",
            self._client.uploadFile(self._resolve_bucket(bucket), object_key, local_path,
                                    metadata=metadata, headers=headers,
                                    progressCallback=progress_callback),
        )
        return ObjectInfo(key=object_key, size=os.path.getsize(local_path),
                          etag=getattr(result.body, "etag", None))

    # ================================================================ 下载
    def download_file(self, key: str, local_path: str,
                      progress_callback: Optional[Callable] = None,
                      bucket: Optional[str] = None) -> str:
        """下载对象到本地文件（自动创建父目录），返回本地路径。"""
        os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)
        self._check(
            "downloadFile",
            self._client.downloadFile(self._resolve_bucket(bucket), key,
                                      downloadFile=local_path,
                                      progressCallback=progress_callback),
        )
        return local_path

    def get_object_bytes(self, key: str, bucket: Optional[str] = None) -> bytes:
        """把对象完整读入内存并返回 ``bytes``（适合中小文件）。"""
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
        """返回流式响应体，适合边下边处理的大文件。

        读取方式：``stream.response.read()``。
        """
        result = self._check(
            "getObject", self._client.getObject(self._resolve_bucket(bucket), key)
        )
        return result.body

    # ================================================================ 查看 / 元数据
    def stat_object(self, key: str, bucket: Optional[str] = None) -> ObjectInfo:
        """返回单个对象的元数据（大小、ETag、修改时间、内容类型）。"""
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
        """判断对象是否存在（404 表示不存在，其余非 2xx 视为错误）。"""
        result = self._client.getObjectMetadata(self._resolve_bucket(bucket), key)
        if getattr(result, "status", None) == 404:
            return False
        self._check("getObjectMetadata", result)
        return True

    def set_object_metadata(self, key: str, metadata: dict,
                            content_type: Optional[str] = None,
                            bucket: Optional[str] = None) -> None:
        """更新对象自定义元数据（注意：会整体覆盖原有元数据）。"""
        headers = {"Content-Type": content_type} if content_type else None
        self._check(
            "setObjectMetadata",
            self._client.setObjectMetadata(self._resolve_bucket(bucket), key,
                                           metadata=metadata, headers=headers),
        )

    def get_object_acl(self, key: str, bucket: Optional[str] = None):
        """返回对象的 ACL 信息（原始响应体，字段随服务端实现而定）。"""
        result = self._check(
            "getObjectAcl",
            self._client.getObjectAcl(self._resolve_bucket(bucket), key),
        )
        return result.body

    # ================================================================ 列表
    def list_objects(self, prefix: Optional[str] = None, delimiter: Optional[str] = None,
                     max_keys: Optional[int] = None, marker: Optional[str] = None,
                     bucket: Optional[str] = None) -> ListResult:
        """列出对象的一页结果。

        :param prefix: 只列以该前缀开头的对象
        :param delimiter: 分组分隔符，传 ``"/"`` 可模拟目录层级
        :param max_keys: 单页最多返回多少个对象
        :param marker: 分页起点（上一页的 ``next_marker``）
        """
        result = self._check(
            "listObjects",
            self._client.listObjects(self._resolve_bucket(bucket), prefix=prefix,
                                     delimiter=delimiter, max_keys=max_keys, marker=marker),
        )
        body = result.body
        # 注意：SDK 里该属性拼写为 commonPrefixs（少一个 e），这里兼容两种写法。
        raw_prefixes = (getattr(body, "commonPrefixs", None)
                        or getattr(body, "commonPrefixes", None) or [])
        return ListResult(
            objects=[ObjectInfo.from_list_entry(c)
                     for c in (getattr(body, "contents", None) or [])],
            prefixes=[getattr(p, "prefix", str(p)) for p in raw_prefixes],
            # 列表级字段是 snake_case，用 _first_attr 兼容可能的 camelCase 变体。
            is_truncated=bool(_first_attr(body, "is_truncated", "isTruncated", default=False)),
            next_marker=_first_attr(body, "next_marker", "nextMarker"),
        )

    def iter_objects(self, prefix: Optional[str] = None, max_keys: Optional[int] = None,
                     bucket: Optional[str] = None) -> Iterator[ObjectInfo]:
        """逐页迭代对象，适合对象数量较多的桶（内存友好）。

        内部自动跟随 ``next_marker`` 翻页，调用方无需关心分页细节。
        """
        marker: Optional[str] = None
        while True:
            page = self.list_objects(prefix=prefix, marker=marker, max_keys=max_keys,
                                     bucket=bucket)
            yield from page.objects
            # 没有下一页（或没有下一页游标）时结束。
            if not page.is_truncated or not page.next_marker:
                break
            marker = page.next_marker

    def list_all_objects(self, prefix: Optional[str] = None,
                         bucket: Optional[str] = None) -> list[ObjectInfo]:
        """一次性列出前缀下所有对象（内部自动翻页）。

        对象很多时建议改用 :meth:`iter_objects`，避免一次性占用大量内存。
        """
        return list(self.iter_objects(prefix=prefix, bucket=bucket))

    def list_prefixes(self, prefix: str = "", bucket: Optional[str] = None) -> list[str]:
        """列出 ``prefix`` 下的“目录”（即公共前缀），等价于只取一层目录。"""
        page = self.list_objects(prefix=prefix, delimiter="/", bucket=bucket)
        return page.prefixes

    # ================================================================ 删除
    def delete_object(self, key: str, bucket: Optional[str] = None) -> None:
        """删除单个对象。"""
        self._check("deleteObject", self._client.deleteObject(self._resolve_bucket(bucket), key))

    def delete_objects(self, keys: Iterable[str], bucket: Optional[str] = None) -> list[str]:
        """批量删除对象（单次请求）。返回实际删除的 key 列表。

        :param keys: 可迭代的 key 集合；空集合时直接返回，不发起请求
        """
        key_list = list(keys)
        if not key_list:
            return []
        # SDK 要求用 DeleteObjectsRequest 包装对象列表。
        request = DeleteObjectsRequest(quiet=False,
                                       objects=[_SdkObject(key=k) for k in key_list])
        result = self._check(
            "deleteObjects",
            self._client.deleteObjects(self._resolve_bucket(bucket), request),
        )
        deleted = getattr(result.body, "deleted", None) or []
        return [getattr(d, "key", str(d)) for d in deleted]

    def delete_prefix(self, prefix: str, bucket: Optional[str] = None) -> int:
        """删除某前缀下的全部对象，返回删除数量。

        先列举出全部 key，再按每批 :data:`_MAX_DELETE_BATCH` 个提交批量删除。
        """
        keys = [info.key for info in self.iter_objects(prefix=prefix, bucket=bucket)]
        for start in range(0, len(keys), _MAX_DELETE_BATCH):
            self.delete_objects(keys[start:start + _MAX_DELETE_BATCH], bucket=bucket)
        return len(keys)

    # ================================================================ 复制
    def copy_object(self, source_key: str, dest_key: str,
                    source_bucket: Optional[str] = None,
                    dest_bucket: Optional[str] = None,
                    metadata: Optional[dict] = None) -> None:
        """在桶内或跨桶复制对象。

        源桶 / 目标桶省略时均使用默认桶，因此同桶复制只需传两个 key。
        """
        self._check(
            "copyObject",
            self._client.copyObject(self._resolve_bucket(source_bucket), source_key,
                                    self._resolve_bucket(dest_bucket), dest_key,
                                    metadata=metadata),
        )

    # ================================================================ 临时访问地址
    def generate_signed_url(self, key: str, method: str = "GET", expires: int = 3600,
                            headers: Optional[dict] = None,
                            query_params: Optional[dict] = None,
                            bucket: Optional[str] = None) -> str:
        """生成对象的临时（预签名）访问地址。

        URL 在 ``expires`` 秒内有效，任何持有者都能按 ``method`` 操作该对象，
        无需 AK/SK。

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
        # 与其他接口不同，预签名结果直接在响应对象上，而不是 result.body。
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
