"""The user's data export (PRD A6; plan 4.3).

- GET /me/export: everything as one JSON document (unchanged; the site's privacy page uses it).
- GET /me/export.zip: the same JSON as data.json, every photo's cleaned original under
  photos/<item_id>/<photo_id>.<ext>, and manifest.json.

The ZIP is built from one consistent snapshot of the database: a single read transaction at
REPEATABLE READ on PostgreSQL. In it, in this order: the photo count and total size, the data
for data.json, the list of photo files. So all three describe the same moment; a photo added
after the snapshot opened is not in the export, and one deleted after it is listed in data.json
but its file may be gone: it is named in missing_photos and does not fail the export.

The file is built in a private temporary file (mode 0600), reading photos from storage in
blocks, and sent only when complete. Any failure while building deletes it at once.

Part (b) of step C adds the limits (size, count, per hour, concurrency, time), the memory
budget tests and the clean-up of temporary files left by a crash.
"""
import json
import os
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import clock
from .config import settings
from .db import SessionLocal, is_sqlite
from .models import Donation, DonationItem, Item, ItemPhoto, ItemStatusChange, Profile, User, WearLog
from .schemas import ItemOut, ProfileBody, UserOut
from .storage import Storage, storage as default_storage

FORMAT = "dolab-export"
VERSION = 1


def export_payload(db: Session, user: User) -> dict:
    """Everything DOLAB stores about one user, as plain JSON values."""
    profile = db.get(Profile, user.id)
    items = list(db.scalars(select(Item).where(Item.user_id == user.id).order_by(Item.added_at, Item.id)))
    wears = db.scalars(select(WearLog).where(WearLog.user_id == user.id).order_by(WearLog.worn_on, WearLog.item_id))
    changes = db.scalars(
        select(ItemStatusChange).where(ItemStatusChange.user_id == user.id)
        .order_by(ItemStatusChange.changed_at, ItemStatusChange.id)
    )
    donations = []
    for donation in db.scalars(
        select(Donation).where(Donation.user_id == user.id).order_by(Donation.confirmed_at, Donation.id)
    ):
        item_ids = db.scalars(select(DonationItem.item_id).where(DonationItem.donation_id == donation.id))
        donations.append(
            {
                "id": donation.id,
                "recipient_type": donation.recipient_type,
                "recipient_name": donation.recipient_name,
                "confirmed_at": donation.confirmed_at.isoformat(),
                "item_ids": sorted(item_ids),
            }
        )
    return {
        "user": UserOut.model_validate(user).model_dump(mode="json"),
        "profile": ProfileBody.model_validate(profile).model_dump(mode="json") if profile else None,
        "items": [ItemOut.model_validate(item).model_dump(mode="json") for item in items],
        "wear_log": [{"item_id": w.item_id, "worn_on": w.worn_on.isoformat()} for w in wears],
        "status_history": [
            {
                "item_id": c.item_id,
                "from_status": c.from_status,
                "to_status": c.to_status,
                "reason": c.reason,
                "ref_id": c.ref_id,
                "changed_at": c.changed_at.isoformat(),
            }
            for c in changes
        ],
        "donations": donations,
        # In the JSON export, photo files are downloaded through the signed links on each
        # item's photo list; the ZIP export carries the files themselves.
        "photos": [
            {
                "id": p.id,
                "item_id": p.item_id,
                "content_type": p.content_type,
                "width": p.width,
                "height": p.height,
                "size_bytes": p.size_bytes,
                "created_at": p.created_at.isoformat(),
            }
            for p in db.scalars(
                select(ItemPhoto).where(ItemPhoto.user_id == user.id).order_by(ItemPhoto.created_at, ItemPhoto.id)
            )
        ],
    }


def _snapshot_session() -> Session:
    db = SessionLocal()
    if not is_sqlite:
        # Every statement of the transaction sees the database as of its first statement.
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    return db


def _after_snapshot() -> None:
    """Tests replace this to change the database from another connection once the snapshot is open."""


def _temporary_file() -> tuple[Path, int]:
    folder = Path(settings.export_tmp_dir)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / f"export-{uuid.uuid4()}.zip"
    # Created private (0600) and new: never an existing file, never readable by others.
    return path, os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)


def build_zip(user_id: str, store: Storage | None = None) -> Path:
    """Build the user's export in a temporary file and return its path. The caller sends and
    then deletes it. Raises StorageUnavailable (having deleted the file) if a photo cannot be read."""
    store = store if store is not None else default_storage
    path, descriptor = _temporary_file()
    try:
        with _snapshot_session() as db:
            # 1. What the snapshot holds (the basis for the limits of part b).
            photo_count, photo_bytes = db.execute(
                select(func.count(ItemPhoto.id), func.coalesce(func.sum(ItemPhoto.size_bytes), 0))
                .where(ItemPhoto.user_id == user_id)
            ).one()
            _after_snapshot()
            # 2. The data, 3. the files to write: same transaction, same moment.
            user = db.get(User, user_id)
            if user is None:
                raise LookupError("user not found")
            data = export_payload(db, user)
            photos = db.execute(
                select(ItemPhoto.id, ItemPhoto.item_id, ItemPhoto.original_key, ItemPhoto.size_bytes)
                .where(ItemPhoto.user_id == user_id)
                .order_by(ItemPhoto.created_at, ItemPhoto.id)
            ).all()
            db.rollback()  # the snapshot is no longer needed: storage is read outside the transaction

        raw = os.fdopen(descriptor, "wb")
        descriptor = -1  # the file object owns it now, and closes it
        with raw, zipfile.ZipFile(raw, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
            data_json = json.dumps(data, ensure_ascii=False, indent=2).encode()
            archive.writestr("data.json", data_json, compress_type=zipfile.ZIP_DEFLATED)
            files = [{"path": "data.json", "bytes": len(data_json)}]
            missing: list[str] = []
            for photo in photos:
                blocks = store.read_chunks(photo.original_key)
                if blocks is None:
                    missing.append(photo.id)  # deleted after the snapshot
                    continue
                name = f"photos/{photo.item_id}/{photo.id}{PurePosixPath(photo.original_key).suffix}"
                written = 0
                # Photos are already compressed: stored as they are.
                with archive.open(name, "w") as out:
                    for block in blocks:
                        out.write(block)
                        written += len(block)
                files.append({"path": name, "bytes": written})
            manifest = {
                "format": FORMAT,
                "version": VERSION,
                "created_at": clock.now().isoformat(),
                "user_id": user_id,
                "snapshot": {"photo_count": photo_count, "photo_bytes": int(photo_bytes)},
                "photos_written": len(files) - 1,
                "missing_photos": missing,
                "files": files,
            }
            archive.writestr("manifest.json", json.dumps(manifest, indent=2).encode(), compress_type=zipfile.ZIP_DEFLATED)
        return path
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        path.unlink(missing_ok=True)
        raise
