from fastapi import APIRouter, HTTPException, Response, UploadFile, status
from sqlalchemy import func, select

from ..config import settings
from ..deps import CurrentUser, Db
from ..media import VARIANTS, InvalidImage, process_image, signature_is_valid, signed_path
from ..models import ItemPhoto, new_id
from ..schemas import PhotoOut
from ..services import delete_photo_files
from ..storage import storage
from .items import _own_item

router = APIRouter(tags=["photos"])

MAX_PHOTOS_PER_ITEM = 6


def _out(photo: ItemPhoto) -> PhotoOut:
    return PhotoOut(
        id=photo.id,
        content_type=photo.content_type,
        width=photo.width,
        height=photo.height,
        size_bytes=photo.size_bytes,
        created_at=photo.created_at,
        url=signed_path(photo.id, "original"),
        thumb_url=signed_path(photo.id, "thumb"),
    )


@router.post("/items/{item_id}/photos", response_model=PhotoOut, status_code=status.HTTP_201_CREATED)
def upload_photo(item_id: str, file: UploadFile, user: CurrentUser, db: Db) -> PhotoOut:
    """Attach a photo to one of the user's items.

    The file is checked by its real content, re-encoded (which removes location and device
    data), and stored privately together with a thumbnail.
    """
    item = _own_item(db, user.id, item_id)
    data = file.file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "photo is too large")
    if not data:
        raise HTTPException(422, "empty file")
    count = db.scalar(select(func.count()).select_from(ItemPhoto).where(ItemPhoto.item_id == item.id))
    if count >= MAX_PHOTOS_PER_ITEM:
        raise HTTPException(status.HTTP_409_CONFLICT, f"an item can have at most {MAX_PHOTOS_PER_ITEM} photos")
    try:
        processed = process_image(data)
    except InvalidImage as error:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(error)) from error

    photo_id = new_id()
    original_key = f"{user.id}/{photo_id}_original.{processed.extension}"
    thumb_key = f"{user.id}/{photo_id}_thumb.jpg"
    storage.save(original_key, processed.original)
    storage.save(thumb_key, processed.thumb)
    photo = ItemPhoto(
        id=photo_id,
        item_id=item.id,
        user_id=user.id,
        original_key=original_key,
        thumb_key=thumb_key,
        content_type=processed.content_type,
        width=processed.width,
        height=processed.height,
        size_bytes=len(processed.original),
    )
    db.add(photo)
    try:
        db.commit()
    except Exception:
        # Do not leave files behind without a database record pointing at them.
        delete_photo_files([photo])
        raise
    return _out(photo)


@router.get("/items/{item_id}/photos", response_model=list[PhotoOut])
def list_photos(item_id: str, user: CurrentUser, db: Db) -> list[PhotoOut]:
    item = _own_item(db, user.id, item_id)
    photos = db.scalars(
        select(ItemPhoto).where(ItemPhoto.item_id == item.id).order_by(ItemPhoto.created_at, ItemPhoto.id)
    )
    return [_out(photo) for photo in photos]


@router.delete("/items/{item_id}/photos/{photo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_photo(item_id: str, photo_id: str, user: CurrentUser, db: Db) -> Response:
    item = _own_item(db, user.id, item_id)
    photo = db.get(ItemPhoto, photo_id)
    if photo is None or photo.item_id != item.id or photo.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "photo not found")
    db.delete(photo)
    db.commit()
    delete_photo_files([photo])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/media/{photo_id}/{variant}")
def get_media(photo_id: str, variant: str, exp: int, sig: str, db: Db) -> Response:
    """Serve a photo file. Access is granted by a short-lived signed link, not by a login,

    so the link can be used in an image tag. Links are only handed to the photo's owner.
    """
    if variant not in VARIANTS or not signature_is_valid(photo_id, variant, exp, sig):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "link is invalid or has expired")
    photo = db.get(ItemPhoto, photo_id)
    if photo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "photo not found")
    key, media_type = (
        (photo.original_key, photo.content_type) if variant == "original" else (photo.thumb_key, "image/jpeg")
    )
    data = storage.read(key)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "photo not found")
    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
    )
