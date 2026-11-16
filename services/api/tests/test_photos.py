import io
import time
from pathlib import Path

from PIL import Image

from app import media
from app.config import settings

SHIRT = {"name": "Classic white shirt", "category": "shirt"}
HOST = "http://testserver"


def make_image(fmt: str = "JPEG", size=(1200, 800), exif: bool = False) -> bytes:
    image = Image.new("RGB", size, (200, 30, 30))
    buffer = io.BytesIO()
    if exif:
        tags = image.getexif()
        tags[0x010F] = "PhoneMaker"
        image.save(buffer, fmt, exif=tags)
    else:
        image.save(buffer, fmt)
    return buffer.getvalue()


def upload(client, headers, item_id, data=None, name="photo.jpg"):
    files = {"file": (name, data if data is not None else make_image(), "image/jpeg")}
    return client.post(f"/items/{item_id}/photos", files=files, headers=headers)


def new_item(client, headers) -> str:
    return client.post("/items", json=SHIRT, headers=headers).json()["id"]


def stored_files(client, headers) -> list[Path]:
    user_id = client.get("/me", headers=headers).json()["id"]
    folder = Path(settings.media_dir) / user_id
    return sorted(folder.iterdir()) if folder.exists() else []


def test_upload_and_view_through_signed_links(client, signup):
    headers = signup()
    item_id = new_item(client, headers)
    response = upload(client, headers, item_id)
    assert response.status_code == 201, response.text
    photo = response.json()
    assert (photo["width"], photo["height"]) == (1200, 800)
    assert photo["content_type"] == "image/jpeg"

    # No login header: the signed link itself is the permission.
    thumb = client.get(HOST + photo["thumb_url"])
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/jpeg"
    assert max(Image.open(io.BytesIO(thumb.content)).size) == media.THUMB_SIZE
    original = client.get(HOST + photo["url"])
    assert Image.open(io.BytesIO(original.content)).size == (1200, 800)

    listed = client.get(f"/items/{item_id}/photos", headers=headers).json()
    assert [p["id"] for p in listed] == [photo["id"]]


def test_item_shows_its_cover_thumbnail(client, signup):
    headers = signup()
    item_id = new_item(client, headers)
    assert client.get(f"/items/{item_id}", headers=headers).json()["cover_thumb_url"] is None
    upload(client, headers, item_id)
    listed = client.get("/items", headers=headers).json()[0]
    assert listed["cover_thumb_url"].startswith("/v1/media/")
    assert client.get(HOST + listed["cover_thumb_url"]).status_code == 200


def test_links_cannot_be_forged_reused_for_another_file_or_used_after_expiry(client, signup):
    headers = signup()
    photo = upload(client, headers, new_item(client, headers)).json()
    thumb_url = photo["thumb_url"]

    forged = thumb_url[:-4] + ("0000" if not thumb_url.endswith("0000") else "1111")
    assert client.get(HOST + forged).status_code == 403
    # A thumbnail signature does not open the original.
    assert client.get(HOST + thumb_url.replace("/thumb?", "/original?")).status_code == 403
    assert client.get(f"{HOST}/v1/media/{photo['id']}/thumb", params={"exp": 9999999999, "sig": "x"}).status_code == 403
    expired = media.signed_path(photo["id"], "thumb", now=time.time() - 3600)
    assert client.get(HOST + expired).status_code == 403


def test_location_and_device_data_is_removed(client, signup):
    headers = signup()
    data = make_image(exif=True)
    assert Image.open(io.BytesIO(data)).getexif()  # the upload really carries EXIF
    photo = upload(client, headers, new_item(client, headers), data).json()
    stored = client.get(HOST + photo["url"]).content
    assert not Image.open(io.BytesIO(stored)).getexif()


def test_files_that_are_not_photos_are_rejected(client, signup):
    headers = signup()
    item_id = new_item(client, headers)
    assert upload(client, headers, item_id, b"<script>alert(1)</script>", "photo.jpg").status_code == 415
    assert upload(client, headers, item_id, make_image("GIF"), "photo.jpg").status_code == 415
    assert upload(client, headers, item_id, b"").status_code == 422
    assert stored_files(client, headers) == []


def test_png_and_webp_are_accepted(client, signup):
    headers = signup()
    item_id = new_item(client, headers)
    assert upload(client, headers, item_id, make_image("PNG"), "a.png").json()["content_type"] == "image/png"
    assert upload(client, headers, item_id, make_image("WEBP"), "a.webp").json()["content_type"] == "image/webp"


def test_oversized_upload_is_rejected(client, signup):
    headers = signup()
    item_id = new_item(client, headers)
    limit = settings.max_upload_bytes
    object.__setattr__(settings, "max_upload_bytes", 1000)  # settings are frozen; lower the limit for this test
    try:
        assert upload(client, headers, item_id).status_code == 413
    finally:
        object.__setattr__(settings, "max_upload_bytes", limit)
    assert stored_files(client, headers) == []


def test_photo_limit_per_item(client, signup):
    headers = signup()
    item_id = new_item(client, headers)
    small = make_image(size=(40, 40))
    for _ in range(6):
        assert upload(client, headers, item_id, small).status_code == 201
    assert upload(client, headers, item_id, small).status_code == 409


def test_other_users_cannot_add_list_or_delete_photos(client, signup):
    owner = signup("owner@example.com")
    other = signup("other@example.com")
    item_id = new_item(client, owner)
    photo_id = upload(client, owner, item_id).json()["id"]
    assert upload(client, other, item_id).status_code == 404
    assert client.get(f"/items/{item_id}/photos", headers=other).status_code == 404
    assert client.delete(f"/items/{item_id}/photos/{photo_id}", headers=other).status_code == 404
    # Nor by attaching the owner's photo id to their own item.
    own_item = new_item(client, other)
    assert client.delete(f"/items/{own_item}/photos/{photo_id}", headers=other).status_code == 404
    assert len(client.get(f"/items/{item_id}/photos", headers=owner).json()) == 1
    assert client.post(f"/items/{item_id}/photos", files={"file": ("a.jpg", make_image(), "image/jpeg")}).status_code == 401


def test_deleting_a_photo_removes_its_files(client, signup):
    headers = signup()
    item_id = new_item(client, headers)
    photo = upload(client, headers, item_id).json()
    assert len(stored_files(client, headers)) == 2
    assert client.delete(f"/items/{item_id}/photos/{photo['id']}", headers=headers).status_code == 204
    assert stored_files(client, headers) == []
    assert client.get(HOST + photo["thumb_url"]).status_code == 404


def test_deleting_an_item_removes_its_photo_files(client, signup):
    headers = signup()
    item_id = new_item(client, headers)
    upload(client, headers, item_id)
    assert client.delete(f"/items/{item_id}", headers=headers).status_code == 204
    assert stored_files(client, headers) == []


def test_deleting_the_account_removes_photo_files(client, signup):
    headers = signup()
    upload(client, headers, new_item(client, headers))
    user_id = client.get("/me", headers=headers).json()["id"]
    assert client.delete("/me", headers=headers).status_code == 204
    folder = Path(settings.media_dir) / user_id
    assert not folder.exists() or list(folder.iterdir()) == []


def test_export_lists_photos(client, signup):
    headers = signup()
    item_id = new_item(client, headers)
    photo_id = upload(client, headers, item_id).json()["id"]
    photos = client.get("/me/export", headers=headers).json()["photos"]
    assert [(p["id"], p["item_id"]) for p in photos] == [(photo_id, item_id)]
