"""Integration tests for the OBS helper against a real S3-compatible endpoint.

These tests exercise the real ``esdk-obs-python`` code paths. Point them at any
S3-compatible object store by exporting:

    OBS_TEST_SERVER=http://127.0.0.1:8333
    OBS_TEST_ACCESS_KEY_ID=any
    OBS_TEST_SECRET_ACCESS_KEY=anysecret

When ``OBS_TEST_SERVER`` is unset the whole module is skipped, so the suite stays
green on machines without an object store available.
"""

from __future__ import annotations

import os
import urllib.request
import uuid

import pytest

from huawei_obs import ObsConfig, ObsError, ObsService

SERVER = os.getenv("OBS_TEST_SERVER")

pytestmark = pytest.mark.skipif(
    not SERVER, reason="set OBS_TEST_SERVER to run OBS integration tests"
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
    # best-effort cleanup
    for info in svc.list_all_objects():
        svc.delete_object(info.key)
    svc.delete_bucket()
    svc.close()


def test_bucket_created_and_listed(service: ObsService, bucket: str):
    assert service.bucket_exists()
    assert bucket in service.list_buckets()


def test_create_bucket_is_idempotent(service: ObsService):
    assert service.create_bucket() is False  # already created by the fixture


def test_upload_and_download_bytes_roundtrip(service: ObsService):
    payload = "hello obs 你好".encode("utf-8")
    info = service.upload_bytes("docs/hello.txt", payload, content_type="text/plain")
    assert info.key == "docs/hello.txt"
    assert info.size == len(payload)
    assert service.get_object_bytes("docs/hello.txt") == payload


def test_upload_and_download_file_roundtrip(service: ObsService, tmp_path):
    src = tmp_path / "report.txt"
    src.write_text("file body", encoding="utf-8")
    info = service.upload_file(str(src), key="reports/report.txt")
    assert info.size == src.stat().st_size

    dest = tmp_path / "out" / "report.txt"
    service.download_file("reports/report.txt", str(dest))
    assert dest.read_text(encoding="utf-8") == "file body"


def test_stat_object(service: ObsService):
    service.upload_bytes("meta.txt", b"12345")
    info = service.stat_object("meta.txt")
    assert info.size == 5
    assert info.content_type == "text/plain"


def test_object_exists(service: ObsService):
    service.upload_bytes("present.txt", b"x")
    assert service.object_exists("present.txt") is True
    assert service.object_exists("absent.txt") is False


def test_list_objects_with_prefix_and_pagination(service: ObsService):
    for i in range(3):
        service.upload_bytes(f"list/item-{i}.txt", b"x")
    keys = [o.key for o in service.list_all_objects(prefix="list/")]
    assert keys == ["list/item-0.txt", "list/item-1.txt", "list/item-2.txt"]

    page = service.list_objects(prefix="list/", max_keys=2)
    assert len(page.objects) == 2


def test_list_prefixes(service: ObsService):
    service.upload_bytes("a/one.txt", b"1")
    service.upload_bytes("b/two.txt", b"2")
    prefixes = service.list_prefixes()
    assert "a/" in prefixes and "b/" in prefixes


def test_signed_get_url_is_usable(service: ObsService):
    service.upload_bytes("signed.txt", b"signed-body")
    url = service.generate_signed_url("signed.txt", method="GET", expires=120)
    assert "X-Amz-Signature" in url or "Signature" in url
    with urllib.request.urlopen(url, timeout=10) as resp:
        assert resp.status == 200
        assert resp.read() == b"signed-body"


def test_signed_put_url_is_usable(service: ObsService):
    url = service.generate_upload_url("signed-upload.txt", expires=120)
    request = urllib.request.Request(url, data=b"uploaded-via-url", method="PUT")
    with urllib.request.urlopen(request, timeout=10) as resp:
        assert resp.status == 200
    assert service.get_object_bytes("signed-upload.txt") == b"uploaded-via-url"


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


def test_missing_object_raises(service: ObsService):
    with pytest.raises(ObsError) as exc:
        service.get_object_bytes("does-not-exist.txt")
    assert exc.value.status == 404
