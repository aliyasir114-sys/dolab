"""Step C-2: one storage interface for disk and S3 (plan 4.6). These run on every backend; the
MinIO-only checks are in test_s3_storage.py."""
import os
import subprocess
from datetime import datetime, timezone
import sys
from pathlib import Path

import pytest
from botocore.stub import Stubber

from app import storage as storage_module
from app.routers import photos as photos_router
from app.services import delete_photo_files
from app.storage import LocalStorage, S3Storage, StorageUnavailable, check_key, storage
from tests.test_photos import make_image, new_item, stored_files, upload

API_DIR = Path(__file__).resolve().parents[1]
MODIFIED = datetime(2026, 11, 23, 8, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("key", ["", "/abs", "../x", "a/../b", "a//b", "a/./b", "a\\b", "a b", ".hidden", "a/" + "x" * 600])
def test_keys_the_server_would_never_make_are_refused(key):
    with pytest.raises(ValueError):
        check_key(key)


def test_good_keys_pass():
    assert check_key("0b9c-11/0b9c_original.jpg") == "0b9c-11/0b9c_original.jpg"


def test_local_storage_round_trip(tmp_path):
    local = LocalStorage(str(tmp_path))
    local.save("u1/a_original.jpg", b"one")
    local.save("u1/a_thumb.jpg", b"two")
    local.save("u2/b_thumb.jpg", b"three")
    assert local.read("u1/a_original.jpg") == b"one"
    assert local.list_keys("u1/") == ["u1/a_original.jpg", "u1/a_thumb.jpg"]
    local.delete("u1/a_original.jpg")
    local.delete("u1/a_original.jpg")  # deleting twice is fine
    assert local.read("u1/a_original.jpg") is None
    assert LocalStorage(str(tmp_path / "missing")).list_keys("") == []


def test_s3_asks_for_server_side_encryption_and_maps_errors():
    import boto3

    client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="y")
    s3 = S3Storage("bucket", client=client, encryption="AES256")
    with Stubber(client) as stub:
        stub.add_response("put_object", {}, {"Bucket": "bucket", "Key": "u/p_thumb.jpg", "Body": b"data",
                                             "ServerSideEncryption": "AES256"})
        s3.save("u/p_thumb.jpg", b"data")
        stub.add_client_error("get_object", "NoSuchKey", http_status_code=404)
        assert s3.read("u/p_thumb.jpg") is None
        stub.add_client_error("delete_object", "AccessDenied", http_status_code=403)
        with pytest.raises(StorageUnavailable):
            s3.delete("u/p_thumb.jpg")
        stub.add_response("list_objects_v2", {"Contents": [{"Key": "u/b", "LastModified": MODIFIED}], "IsTruncated": True, "NextContinuationToken": "t"},
                          {"Bucket": "bucket", "Prefix": "u/"})
        stub.add_response("list_objects_v2", {"Contents": [{"Key": "u/a", "LastModified": MODIFIED}], "IsTruncated": False},
                          {"Bucket": "bucket", "Prefix": "u/", "ContinuationToken": "t"})
        assert s3.list_keys("u/") == ["u/a", "u/b"]
        stub.assert_no_pending_responses()
    with pytest.raises(ValueError):
        s3.save("../escape", b"x")


def test_an_unreachable_storage_is_unavailable_not_a_crash():
    s3 = S3Storage("bucket", endpoint_url="http://127.0.0.1:9", encryption=None)
    with pytest.raises(StorageUnavailable):
        s3.read("u/p_thumb.jpg")


# --- the API's answers when storage fails ---------------------------------------------

def test_reading_a_photo_while_storage_is_down_is_503(client, signup, monkeypatch):
    headers = signup()
    photo = upload(client, headers, new_item(client, headers)).json()

    def down(_key):
        raise StorageUnavailable("down")

    monkeypatch.setattr(storage, "read", down)
    response = client.get(photo["thumb_url"].removeprefix("/v1"))
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"


def test_uploading_while_storage_is_down_is_503_and_leaves_nothing(client, signup, monkeypatch):
    headers = signup()
    item_id = new_item(client, headers)
    saved: list[str] = []
    real_save = storage.save

    def thumb_fails(key, data):
        if key.endswith("_thumb.jpg"):
            raise StorageUnavailable("down")
        saved.append(key)
        real_save(key, data)

    monkeypatch.setattr(storage, "save", thumb_fails)
    response = upload(client, headers, item_id)
    assert response.status_code == 503
    assert saved and stored_files(client, headers) == []  # the original was removed again
    assert client.get(f"/items/{item_id}/photos", headers=headers).json() == []


def test_a_failed_file_delete_does_not_fail_the_users_delete(client, signup, monkeypatch, caplog):
    headers = signup()
    item_id = new_item(client, headers)
    photo = upload(client, headers, item_id).json()

    def down(_key):
        raise StorageUnavailable("down")

    monkeypatch.setattr(storage, "delete", down)
    assert client.delete(f"/items/{item_id}/photos/{photo['id']}", headers=headers).status_code == 204
    assert client.get(f"/items/{item_id}/photos", headers=headers).json() == []
    # The old link stops working at once, because the record is gone.
    assert client.get(photo["url"].removeprefix("/v1")).status_code == 404
    assert "left in storage" in caplog.text
    assert delete_photo_files([]) is None


# --- settings ------------------------------------------------------------------------

def _load(env: dict[str, str]) -> subprocess.CompletedProcess:
    full = {k: v for k, v in os.environ.items() if not k.startswith(("DOLAB_S3", "DOLAB_STORAGE", "DOLAB_ENV"))}
    full.update(env)
    code = "from app.config import settings as s; print(s.storage, s.s3_bucket, s.s3_encryption, s.s3_endpoint_url)"
    return subprocess.run([sys.executable, "-c", code], cwd=API_DIR, env=full, capture_output=True, text=True)


def test_storage_settings_are_checked():
    production = {"DOLAB_ENV": "production", "DOLAB_JWT_SECRET": "x" * 40,
                  "DOLAB_DATABASE_URL": "postgresql+psycopg://u:p@localhost/dolab"}
    assert "must be 's3' in production" in _load(production).stderr
    assert "DOLAB_S3_BUCKET" in _load({**production, "DOLAB_STORAGE": "s3"}).stderr
    no_encryption = _load({**production, "DOLAB_STORAGE": "s3", "DOLAB_S3_BUCKET": "b", "DOLAB_S3_ENCRYPTION": "none"})
    assert "cannot be 'none' in production" in no_encryption.stderr
    good = _load({**production, "DOLAB_STORAGE": "s3", "DOLAB_S3_BUCKET": "b"})
    assert good.returncode == 0, good.stderr
    assert good.stdout.split() == ["s3", "b", "AES256", "None"]  # encrypted and AWS default when endpoint is unset
    assert "DOLAB_STORAGE must be" in _load({"DOLAB_STORAGE": "ftp"}).stderr
    assert _load({}).stdout.split()[0] == "local"


def test_s3_endpoint_https_is_required_only_in_production():
    production = {"DOLAB_ENV": "production", "DOLAB_JWT_SECRET": "x" * 40,
                  "DOLAB_DATABASE_URL": "postgresql+psycopg://u:p@localhost/dolab",
                  "DOLAB_STORAGE": "s3", "DOLAB_S3_BUCKET": "b"}

    http = _load({**production, "DOLAB_S3_ENDPOINT_URL": "http://s3.example.test"})
    assert "DOLAB_S3_ENDPOINT_URL must use https:// in production" in http.stderr

    https = _load({**production, "DOLAB_S3_ENDPOINT_URL": "https://s3.example.test"})
    assert https.returncode == 0, https.stderr
    assert https.stdout.split()[-1] == "https://s3.example.test"


@pytest.mark.parametrize("environment", ["test", "development"])
def test_http_s3_endpoint_remains_compatible_outside_production(environment):
    result = _load({"DOLAB_ENV": environment,
                    "DOLAB_STORAGE": "s3",
                    "DOLAB_S3_BUCKET": "b",
                    "DOLAB_S3_ENDPOINT_URL": "http://minio:9000"})
    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[-1] == "http://minio:9000"


def test_the_configured_backend_is_used():
    expected = S3Storage if os.getenv("DOLAB_STORAGE") == "s3" else LocalStorage
    assert isinstance(storage_module.storage, expected)
    assert photos_router.storage is storage_module.storage
