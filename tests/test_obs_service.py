"""针对 OBS 操作层的集成测试（对接真实对象存储，不使用 mock）。

默认对接任意 S3 兼容端点；通过环境变量指定：

    OBS_TEST_SERVER=http://127.0.0.1:8333
    OBS_TEST_ACCESS_KEY_ID=any
    OBS_TEST_SECRET_ACCESS_KEY=anysecret

未设置 ``OBS_TEST_SERVER`` 时整个模块自动跳过，保证没有对象存储的机器也能通过。
"""

from __future__ import annotations

import os
import urllib.request
import uuid

import pytest

from huawei_obs import ObsConfig, ObsError, ObsService

SERVER = os.getenv("OBS_TEST_SERVER")

pytestmark = pytest.mark.skipif(
    not SERVER, reason="设置 OBS_TEST_SERVER 后才会运行 OBS 集成测试"
)


def _config(bucket: str) -> ObsConfig:
    return ObsConfig(
        access_key_id=os.getenv("OBS_TEST_ACCESS_KEY_ID", "any"),
        secret_access_key=os.getenv("OBS_TEST_SECRET_ACCESS_KEY", "anysecret"),
        server=SERVER,
        bucket=bucket,
        region="region",
        signature="v4",
        path_style=True,
        ssl_verify=False,
    )


@pytest.fixture(scope="module")
def bucket() -> str:
    return f"test-{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def service(bucket: str):
    svc = ObsService(_config(bucket))
    svc.create_bucket()
    yield svc
    svc.delete_prefix("")          # 清空桶内对象
    svc.delete_bucket()
    svc.close()


# --------------------------------------------------------------------- 桶
def test_bucket_created_and_listed(service: ObsService, bucket: str):
    assert service.bucket_exists()
    assert bucket in service.list_buckets()


def test_create_bucket_is_idempotent(service: ObsService):
    assert service.create_bucket() is False  # fixture 已创建


def test_get_bucket_location(service: ObsService):
    # S3 兼容端点可能返回空区域，这里只验证调用不报错
    service.get_bucket_location()


# --------------------------------------------------------------- 上传下载
def test_upload_and_read_bytes_roundtrip(service: ObsService):
    payload = "hello obs 你好".encode("utf-8")
    info = service.upload_bytes("docs/hello.txt", payload, content_type="text/plain")
    assert info.key == "docs/hello.txt"
    assert info.size == len(payload)
    assert service.get_object_bytes("docs/hello.txt") == payload
    assert service.get_object_text("docs/hello.txt") == "hello obs 你好"


def test_upload_and_download_file_roundtrip(service: ObsService, tmp_path):
    src = tmp_path / "report.txt"
    src.write_text("file body", encoding="utf-8")
    info = service.upload_file(str(src), key="reports/report.txt")
    assert info.size == src.stat().st_size

    dest = tmp_path / "out" / "report.txt"  # 目录不存在，应自动创建
    service.download_file("reports/report.txt", str(dest))
    assert dest.read_text(encoding="utf-8") == "file body"


def test_get_object_stream(service: ObsService):
    service.upload_bytes("stream.txt", b"stream-body")
    body = service.get_object_stream("stream.txt")
    assert body.response.read() == b"stream-body"


# --------------------------------------------------------------- 查看
def test_stat_object(service: ObsService):
    service.upload_bytes("meta.txt", b"12345", content_type="text/plain")
    info = service.stat_object("meta.txt")
    assert info.size == 5
    assert info.content_type == "text/plain"


def test_object_exists(service: ObsService):
    service.upload_bytes("present.txt", b"x")
    assert service.object_exists("present.txt") is True
    assert service.object_exists("absent.txt") is False


def test_set_object_metadata(service: ObsService):
    service.upload_bytes("meta-set.txt", b"data")
    service.set_object_metadata("meta-set.txt", {"x-custom": "1"})
    assert service.object_exists("meta-set.txt")


def test_get_object_acl(service: ObsService):
    service.upload_bytes("acl.txt", b"data")
    acl = service.get_object_acl("acl.txt")
    assert acl is not None


# --------------------------------------------------------------- 列表
def test_list_objects_with_prefix(service: ObsService):
    for i in range(3):
        service.upload_bytes(f"list/item-{i}.txt", b"x")
    keys = [o.key for o in service.list_all_objects(prefix="list/")]
    assert keys == ["list/item-0.txt", "list/item-1.txt", "list/item-2.txt"]


def test_list_objects_pagination(service: ObsService):
    for i in range(5):
        service.upload_bytes(f"page/p-{i}.txt", b"x")
    page = service.list_objects(prefix="page/", max_keys=2)
    assert len(page.objects) == 2
    assert page.is_truncated is True
    # 迭代器应自动翻页拿全
    assert len(list(service.iter_objects(prefix="page/", max_keys=2))) == 5


def test_list_prefixes(service: ObsService):
    service.upload_bytes("a/one.txt", b"1")
    service.upload_bytes("b/two.txt", b"2")
    prefixes = service.list_prefixes()
    assert "a/" in prefixes and "b/" in prefixes


# --------------------------------------------------------------- 临时地址
def test_signed_get_url_is_usable(service: ObsService):
    service.upload_bytes("signed.txt", b"signed-body")
    url = service.generate_download_url("signed.txt", expires=120)
    assert "Signature" in url
    with urllib.request.urlopen(url, timeout=10) as resp:
        assert resp.status == 200
        assert resp.read() == b"signed-body"


def test_signed_put_url_is_usable(service: ObsService):
    url = service.generate_upload_url("signed-upload.txt", expires=120)
    request = urllib.request.Request(url, data=b"uploaded-via-url", method="PUT")
    with urllib.request.urlopen(request, timeout=10) as resp:
        assert resp.status == 200
    assert service.get_object_bytes("signed-upload.txt") == b"uploaded-via-url"


def test_signed_url_rejects_bad_method(service: ObsService):
    with pytest.raises(ValueError):
        service.generate_signed_url("signed.txt", method="TRACE")


# --------------------------------------------------------------- 复制删除
def test_copy_object(service: ObsService):
    service.upload_bytes("orig.txt", b"original")
    service.copy_object("orig.txt", "copy.txt")
    assert service.get_object_bytes("copy.txt") == b"original"


def test_delete_object(service: ObsService):
    service.upload_bytes("gone.txt", b"bye")
    service.delete_object("gone.txt")
    assert service.object_exists("gone.txt") is False


def test_batch_delete(service: ObsService):
    for i in range(3):
        service.upload_bytes(f"batch/{i}.txt", b"x")
    deleted = service.delete_objects(["batch/0.txt", "batch/1.txt", "batch/2.txt"])
    assert sorted(deleted) == ["batch/0.txt", "batch/1.txt", "batch/2.txt"]
    assert service.list_all_objects(prefix="batch/") == []


def test_delete_prefix(service: ObsService):
    for i in range(4):
        service.upload_bytes(f"purge/f-{i}.txt", b"x")
    assert service.delete_prefix("purge/") == 4
    assert service.list_all_objects(prefix="purge/") == []


def test_missing_object_raises(service: ObsService):
    with pytest.raises(ObsError) as exc:
        service.get_object_bytes("does-not-exist.txt")
    assert exc.value.status == 404
