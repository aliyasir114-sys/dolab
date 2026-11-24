"""The user's data export (PRD A6; plan 4.3).

- GET /me/export: everything as one JSON document (the site's privacy page uses it).
- GET /me/export.zip: the same JSON as data.json, every photo's cleaned original under
  photos/<item_id>/<photo_id>.<ext>, and manifest.json.

Consistency. The ZIP is built from one snapshot of the database: a single read transaction
(REPEATABLE READ on PostgreSQL, an explicit read transaction on SQLite). In it, in this order:
the photo count and total size (and the limits checked against them), the data for data.json,
the list of photo files. A photo added after the snapshot opened is not in the export; one
deleted after it is listed in data.json, its file may be gone, and it is named in
missing_photos without failing the export.

Limits (part b), all checked before any photo is read, from the same snapshot:
  DOLAB_EXPORT_MAX_BYTES (500 MB of photos), DOLAB_EXPORT_MAX_FILES (3000 photos),
  DOLAB_EXPORT_MAX_DATA_BYTES (8 MB of data.json) -> ExportTooLarge (413).
The route adds DOLAB_EXPORT_PER_HOUR (3 per user, shared, 429) and DOLAB_EXPORT_CONCURRENCY
(2 at once per instance, 503). An export holds its place from the start of the build until its
file has been sent, or the client has gone (review of PR #48).

Time. DOLAB_EXPORT_BUILD_SECONDS (120): the build runs in the request's own thread and checks
its deadline between steps and between blocks; on PostgreSQL, statement_timeout is set to the
time left before every statement and again while data.json streams (each fetch of the cursor
is a statement too), so no query can run past the deadline (review of PR #48). Past the deadline the build stops where it is and its file is
deleted (ExportTimeout, 503). A single storage read in progress can overrun by at most its own
timeout (30 s on S3).

Memory: at most 16 MB per export. data.json is written row by row as it is read, never built
whole in memory; photos are read in 1 MB blocks; the ZIP index grows with the number of files
(about 1 KB each), hence the 3000-photo limit.

Temporary files: export-<uuid>.zip in DOLAB_EXPORT_TMP_DIR, mode 0600, never uploaded. Deleted
at once if the build fails, and when sending ends (also if the client disconnects). Files a
crash leaves behind: removed at start-up (untouched for 10 minutes) and every 10 minutes (older
than an hour). Files this process is still building or sending are never removed.
"""
import json
import logging
import os
import threading
import time
import uuid
import zipfile
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path, PurePosixPath

from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from starlette.responses import FileResponse

from . import clock
from .config import settings
from .db import SessionLocal, is_sqlite
from .models import Donation, DonationItem, Item, ItemPhoto, ItemStatusChange, Profile, User, WearLog
from .schemas import ItemOut, ProfileBody, UserOut
from .storage import Storage, storage as default_storage

log = logging.getLogger("dolab.export")

FORMAT = "dolab-export"
VERSION = 1
_ROWS = 500  # rows fetched at a time while writing data.json


class ExportTooLarge(Exception):
    """Over a limit; nothing was read from storage. The message says which."""


class ExportTimeout(Exception):
    """The build ran past DOLAB_EXPORT_BUILD_SECONDS and was stopped."""


class ExportBusy(Exception):
    """This server instance already has DOLAB_EXPORT_CONCURRENCY exports building or sending."""


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _rows(db: Session, statement) -> Iterator:
    """ORM rows a few hundred at a time; each batch is let go once written."""
    for batch in db.scalars(statement.execution_options(yield_per=_ROWS)).partitions():
        yield from batch
        for row in batch:
            db.expunge(row)


def _photo_json(p: ItemPhoto) -> dict:
    return {"id": p.id, "item_id": p.item_id, "content_type": p.content_type, "width": p.width,
            "height": p.height, "size_bytes": p.size_bytes, "created_at": p.created_at.isoformat()}


def write_export_json(db: Session, user: User, write: Callable[[str], None]) -> None:
    """Everything DOLAB stores about one user, written out as JSON piece by piece."""
    write('{"user":' + _dumps(UserOut.model_validate(user).model_dump(mode="json")))
    profile = db.get(Profile, user.id)
    write(',"profile":' + _dumps(ProfileBody.model_validate(profile).model_dump(mode="json") if profile else None))

    def array(name: str, statement, convert) -> None:
        write(f',"{name}":[')
        for n, row in enumerate(_rows(db, statement)):
            write(("," if n else "") + "\n" + _dumps(convert(row)))
        write("]")

    array("items", select(Item).where(Item.user_id == user.id).order_by(Item.added_at, Item.id),
          lambda item: ItemOut.model_validate(item).model_dump(mode="json"))
    array("wear_log", select(WearLog).where(WearLog.user_id == user.id).order_by(WearLog.worn_on, WearLog.item_id),
          lambda w: {"item_id": w.item_id, "worn_on": w.worn_on.isoformat()})
    array("status_history",
          select(ItemStatusChange).where(ItemStatusChange.user_id == user.id)
          .order_by(ItemStatusChange.changed_at, ItemStatusChange.id),
          lambda c: {"item_id": c.item_id, "from_status": c.from_status, "to_status": c.to_status,
                     "reason": c.reason, "ref_id": c.ref_id, "changed_at": c.changed_at.isoformat()})
    array("donations", select(Donation).where(Donation.user_id == user.id).order_by(Donation.confirmed_at, Donation.id),
          lambda d: {"id": d.id, "recipient_type": d.recipient_type, "recipient_name": d.recipient_name,
                     "confirmed_at": d.confirmed_at.isoformat(),
                     "item_ids": sorted(db.scalars(select(DonationItem.item_id).where(DonationItem.donation_id == d.id)))})
    # In the JSON export, photo files are downloaded through the signed links on each item's
    # photo list; the ZIP export carries the files themselves.
    array("photos", select(ItemPhoto).where(ItemPhoto.user_id == user.id).order_by(ItemPhoto.created_at, ItemPhoto.id),
          _photo_json)
    write("}\n")


def export_payload(db: Session, user: User) -> dict:
    """The same document as a value, for GET /me/export."""
    parts: list[str] = []
    write_export_json(db, user, parts.append)
    return json.loads("".join(parts))


def _snapshot_session(seconds_left: Callable[[], float]) -> tuple[Session, Callable[[], None]]:
    """The export's read transaction, and a function that brings PostgreSQL's statement_timeout
    down to the time left (call it while streaming; it is also called before every statement)."""
    db = SessionLocal()
    if is_sqlite:
        # SQLite does not begin a read transaction for SELECTs on its own.  An explicit
        # transaction makes the first count establish a snapshot shared by every later SELECT.
        db.connection().exec_driver_sql("BEGIN")
        return db, lambda: None
    # Every statement of the transaction sees the database as of its first statement.
    connection = db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    driver = connection.connection.driver_connection
    last = {"ms": None}

    def limit_statements(force: bool = False) -> None:
        # seconds_left() raises ExportTimeout once the deadline has passed.
        ms = max(1, int(seconds_left() * 1000))
        # While streaming, only when the limit has moved by a quarter of a second or more.
        if force or last["ms"] is None or last["ms"] - ms >= 250:
            # On the driver's own connection: same transaction, and no recursion into the
            # event below. SET takes no snapshot.
            driver.execute(f"SET LOCAL statement_timeout = {ms}")
            last["ms"] = ms

    event.listen(connection, "before_cursor_execute", lambda *args, **kwargs: limit_statements(force=True))
    return db, limit_statements


def _after_snapshot() -> None:
    """Tests replace this to change the database from another connection once the snapshot is open."""


# Exports this process is building or sending, by file path, with when each began. Their number
# is the concurrency limit, and the clean-up never touches their files.
_active: dict[str, float] = {}
_active_lock = threading.Lock()
# A place still held after this long was lost (a response that was never sent): given back.
STALE_AFTER = timedelta(hours=6)


def active_files() -> set[str]:
    with _active_lock:
        return set(_active)


def _prune_stale(now: float) -> list[str]:
    stale = [p for p, began in _active.items() if now - began > STALE_AFTER.total_seconds()]
    for path in stale:
        del _active[path]
    return stale


def reserve() -> Path:
    """Take one of this instance's export places and name the file it will use. Raises
    ExportBusy when all are taken. The place is held until release_file(): after the file has
    been sent (TemporaryFileResponse), or at once if the export fails."""
    with _active_lock:
        stale = _prune_stale(time.monotonic())
        if len(_active) >= settings.export_concurrency:
            raise ExportBusy("too many exports in progress")
        path = Path(settings.export_tmp_dir) / f"export-{uuid.uuid4()}.zip"
        _active[str(path)] = time.monotonic()
    for lost in stale:
        log.warning("export place held for over %s given back: %s", STALE_AFTER, Path(lost).name)
        Path(lost).unlink(missing_ok=True)
    return path


def _temporary_file(path: Path) -> int:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Created private (0600) and new: never an existing file, never readable by others.
    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)


def release_file(path: Path) -> None:
    """Delete a finished or failed export and give its place back."""
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        log.warning("export file could not be deleted, the clean-up will retry: %s (%s)", path.name, error)
    finally:
        with _active_lock:
            _active.pop(str(path), None)


def clean_temporary(older_than: timedelta, now: float | None = None) -> list[str]:
    """Delete export files untouched for longer than `older_than`, except those this process
    is still building or sending. A file being written is touched continuously; one being sent
    is already open, so removing its name would not cut the download short either."""
    folder = Path(settings.export_tmp_dir)
    if not folder.is_dir():
        return []
    now = time.time() if now is None else now
    protected = active_files()
    removed = []
    for path in folder.glob("export-*.zip"):
        if str(path) in protected:
            continue
        try:
            if now - path.stat().st_mtime > older_than.total_seconds():
                path.unlink()
                removed.append(path.name)
        except FileNotFoundError:
            continue
        except OSError as error:
            log.warning("old export file could not be deleted: %s (%s)", path.name, error)
    if removed:
        log.info("removed %d old export file(s)", len(removed))
    return removed


STARTUP_AGE = timedelta(minutes=10)  # longer than any build may run
PERIODIC_AGE = timedelta(hours=1)
PERIOD = timedelta(minutes=10)


class Cleaner:
    """Start-up clean-up, then every PERIOD, in a background thread of this server process."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        clean_temporary(STARTUP_AGE)
        self._thread = threading.Thread(target=self._run, name="export-cleaner", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(PERIOD.total_seconds()):
            try:
                clean_temporary(PERIODIC_AGE)
            except Exception:  # never let the cleaner die
                log.exception("export clean-up failed")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


class TemporaryFileResponse(FileResponse):
    """Sends the export, then deletes it and gives its place back, whether the download finished
    or the client left."""

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            release_file(Path(self.path))


def build_zip(user_id: str, store: Storage | None = None, *, deadline: float | None = None,
              path: Path | None = None) -> Path:
    """Build the user's export in a temporary file and return its path; the caller sends it with
    TemporaryFileResponse (or calls release_file). `path` is a place from reserve(); without one,
    a place is reserved here (ExportBusy if none is free). Raises, having deleted the file and
    given the place back: ExportTooLarge before any photo is read, ExportTimeout past the
    deadline (time.monotonic), StorageUnavailable if a photo cannot be read."""
    store = store if store is not None else default_storage
    deadline = time.monotonic() + settings.export_build_seconds if deadline is None else deadline

    def in_time() -> float:
        left = deadline - time.monotonic()
        if left <= 0:
            raise ExportTimeout("the export took too long")
        return left

    path = path if path is not None else reserve()
    descriptor = -1
    try:
        in_time()
        descriptor = _temporary_file(path)
        db, limit_statements = _snapshot_session(in_time)
        with db:
            # 1. What the snapshot holds, and the limits, before anything else is read.
            photo_count, photo_bytes = db.execute(
                select(func.count(ItemPhoto.id), func.coalesce(func.sum(ItemPhoto.size_bytes), 0))
                .where(ItemPhoto.user_id == user_id)
            ).one()
            photo_bytes = int(photo_bytes)
            _after_snapshot()
            if photo_count > settings.export_max_files:
                raise ExportTooLarge(f"more than {settings.export_max_files} photos")
            if photo_bytes > settings.export_max_bytes:
                raise ExportTooLarge(f"photos larger than {settings.export_max_bytes // (1024 * 1024)} MB in total")
            user = db.get(User, user_id)
            if user is None:
                raise LookupError("user not found")

            raw = os.fdopen(descriptor, "wb")
            descriptor = -1  # the file object owns it now, and closes it
            with raw, zipfile.ZipFile(raw, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
                # 2. data.json, written as it is read, within its own limit.
                data_bytes = 0
                info = zipfile.ZipInfo("data.json", date_time=time.localtime()[:6])
                info.compress_type = zipfile.ZIP_DEFLATED
                with archive.open(info, "w") as out:

                    def write(piece: str) -> None:
                        nonlocal data_bytes
                        block = piece.encode()
                        data_bytes += len(block)
                        if data_bytes > settings.export_max_data_bytes:
                            raise ExportTooLarge(
                                f"data larger than {settings.export_max_data_bytes // (1024 * 1024)} MB")
                        limit_statements()  # also checks the deadline
                        out.write(block)

                    write_export_json(db, user, write)
                # 3. The files to write: same transaction, same moment.
                photos = db.execute(
                    select(ItemPhoto.id, ItemPhoto.item_id, ItemPhoto.original_key)
                    .where(ItemPhoto.user_id == user_id)
                    .order_by(ItemPhoto.created_at, ItemPhoto.id)
                ).all()
                db.rollback()  # the snapshot is no longer needed: storage is read outside it

                files = [{"path": "data.json", "bytes": data_bytes}]
                missing: list[str] = []
                for photo in photos:
                    in_time()
                    blocks = store.read_chunks(photo.original_key)
                    if blocks is None:
                        missing.append(photo.id)  # deleted after the snapshot
                        continue
                    name = f"photos/{photo.item_id}/{photo.id}{PurePosixPath(photo.original_key).suffix}"
                    written = 0
                    # Photos are already compressed: stored as they are.
                    with archive.open(name, "w") as out:
                        for block in blocks:
                            in_time()
                            out.write(block)
                            written += len(block)
                    files.append({"path": name, "bytes": written})
                manifest = {
                    "format": FORMAT,
                    "version": VERSION,
                    "created_at": clock.now().isoformat(),
                    "user_id": user_id,
                    "snapshot": {"photo_count": photo_count, "photo_bytes": photo_bytes},
                    "photos_written": len(files) - 1,
                    "missing_photos": missing,
                    "files": files,
                }
                archive.writestr("manifest.json", json.dumps(manifest, indent=2).encode(),
                                 compress_type=zipfile.ZIP_DEFLATED)
        return path
    except DBAPIError as error:
        _discard(path, descriptor)
        if getattr(getattr(error, "orig", None), "sqlstate", None) == "57014":  # statement_timeout
            raise ExportTimeout("the export took too long") from error
        raise
    except BaseException:
        _discard(path, descriptor)
        raise


def _discard(path: Path, descriptor: int) -> None:
    if descriptor >= 0:
        os.close(descriptor)
    release_file(path)
