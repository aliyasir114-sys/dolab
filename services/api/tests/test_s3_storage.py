"""Step C-2 on real S3-compatible storage (MinIO in CI): private bucket, B1, listing."""
import os

import httpx
import pytest

from app.storage import S3Storage, storage
from tests.test_photos import new_item, upload

pytestmark = pytest.mark.skipif(not isinstance(storage, S3Storage), reason="needs DOLAB_STORAGE=s3 (MinIO in CI)")


def _exists(key: str) -> bool:
    return storage.read(key) is not None


def test_the_bucket_is_not_readable_without_a_signature(client, signup):
    headers = signup()
    upload(client, headers, new_item(client, headers))
    user_id = client.get("/me", headers=headers).json()["id"]
    key = storage.list_keys(f"{user_id}/")[0]
    endpoint = os.environ["DOLAB_S3_ENDPOINT_URL"].rstrip("/")
    anonymous = httpx.get(f"{endpoint}/{storage.bucket}/{key}", timeout=10)
    assert anonymous.status_code == 403
    listing = httpx.get(f"{endpoint}/{storage.bucket}?list-type=2", timeout=10)
    assert listing.status_code == 403


def test_b1_deleting_a_photo_leaves_no_file_and_the_old_link_answers_404(client, signup):
    headers = signup()
    item_id = new_item(client, headers)
    photo = upload(client, headers, item_id).json()
    user_id = client.get("/me", headers=headers).json()["id"]
    keys = storage.list_keys(f"{user_id}/")
    assert len(keys) == 2 and all(_exists(k) for k in keys)
    assert client.get(photo["url"].removeprefix("/v1")).status_code == 200

    assert client.delete(f"/items/{item_id}/photos/{photo['id']}", headers=headers).status_code == 204
    assert storage.list_keys(f"{user_id}/") == []
    assert not any(_exists(k) for k in keys)  # neither the original nor the thumbnail
    assert client.get(photo["url"].removeprefix("/v1")).status_code == 404
    assert client.get(photo["thumb_url"].removeprefix("/v1")).status_code == 404


def test_missing_objects_read_as_none_and_delete_quietly():
    assert storage.read("nobody/nothing_thumb.jpg") is None
    storage.delete("nobody/nothing_thumb.jpg")


def test_listing_pages_through_many_keys():
    prefix = "listing-test/"
    keys = [f"{prefix}k{n:04d}" for n in range(1005)]  # more than one page of 1000
    for key in keys:
        storage.save(key, b"x")
    try:
        assert storage.list_keys(prefix) == keys
    finally:
        for key in keys:
            storage.delete(key)
    assert storage.list_keys(prefix) == []
