"""Delete photo files that no database record points at (plan 4.6; F4 in 4.10.3).

Such files appear when a file delete fails after its record is gone (services.delete_photo_files
logs and moves on), when an upload saved its files but its database commit failed, or when a
server stopped half-way.

    python -m app.jobs.orphans [--dry-run] [--batch 500] [--min-age-minutes 60]

Safety rules:
1. Inventory first, complete. The storage is listed in full, then the database is asked about
   every listed key. If either fails, the job stops before deleting anything (exit code 2).
2. Recent files are left alone. An upload saves its files before committing their record, so a
   file younger than --min-age-minutes (default 60) is never treated as an orphan.
3. Bounded. At most --batch files (default 500) are deleted per run; the rest are reported and
   left for the next run.
4. Safe to run again, or twice at once. A record is only ever deleted, never re-created with the
   same key (keys contain a new random id), so a file judged an orphan stays one; deleting a
   file that is already gone is not an error. One failed delete does not stop the others; it is
   reported (exit code 1) and retried by the next run.
5. Only photo files are touched: keys shaped "<user id>/<photo id>_original|thumb.<ext>". Any
   other object in the bucket is ignored.
"""
import argparse
import logging
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import clock
from ..db import SessionLocal
from ..models import ItemPhoto
from ..storage import Storage, StorageUnavailable, storage as default_storage

log = logging.getLogger("dolab.jobs.orphans")

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
PHOTO_KEY = re.compile(rf"^{_UUID}/{_UUID}_(original\.(jpg|png|webp)|thumb\.jpg)$")
DEFAULT_BATCH = 500
DEFAULT_MIN_AGE = timedelta(minutes=60)
# Keys per database query: keeps each IN list small.
_LOOKUP_CHUNK = 500


class InventoryFailed(RuntimeError):
    """The storage or the database could not be fully listed; nothing was deleted."""


@dataclass
class Report:
    listed: int = 0
    ignored_other: int = 0  # not shaped like a photo file
    too_recent: int = 0
    linked: int = 0
    orphans: int = 0
    deleted: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    remaining: int = 0  # orphans left for the next run by the batch limit
    dry_run: bool = False

    def summary(self) -> str:
        verb = "would delete" if self.dry_run else "deleted"
        return (f"listed {self.listed}, other {self.ignored_other}, recent {self.too_recent}, linked {self.linked}, "
                f"orphans {self.orphans}: {verb} {len(self.deleted)}, failed {len(self.failed)}, "
                f"left for next run {self.remaining}")


def _linked_keys(db: Session, keys: list[str]) -> set[str]:
    linked: set[str] = set()
    for start in range(0, len(keys), _LOOKUP_CHUNK):
        chunk = keys[start : start + _LOOKUP_CHUNK]
        rows = db.execute(
            select(ItemPhoto.original_key, ItemPhoto.thumb_key)
            .where(or_(ItemPhoto.original_key.in_(chunk), ItemPhoto.thumb_key.in_(chunk)))
        ).all()
        for original, thumb in rows:
            linked.add(original)
            linked.add(thumb)
    return linked


def sweep(
    store: Storage | None = None,
    *,
    sessions: Callable[[], Session] = SessionLocal,
    batch: int = DEFAULT_BATCH,
    min_age: timedelta = DEFAULT_MIN_AGE,
    dry_run: bool = False,
    now: datetime | None = None,
) -> Report:
    """One run. Raises InventoryFailed (having deleted nothing) if either inventory fails."""
    if batch < 1:
        raise ValueError("batch must be at least 1")
    store = store if store is not None else default_storage
    now = now if now is not None else clock.now()
    report = Report(dry_run=dry_run)

    # 1. Storage inventory, complete or nothing.
    try:
        objects = store.list_objects("")
    except Exception as error:  # storage down, permissions, a page that failed part-way
        raise InventoryFailed(f"storage could not be listed: {error}") from error
    report.listed = len(objects)

    candidates: list[str] = []
    for obj in objects:
        if not PHOTO_KEY.match(obj.key):
            report.ignored_other += 1
        elif now - obj.modified < min_age:
            report.too_recent += 1
        else:
            candidates.append(obj.key)

    # 2. Database inventory for those keys, complete or nothing.
    try:
        with sessions() as db:
            linked = _linked_keys(db, candidates)
    except Exception as error:
        raise InventoryFailed(f"database could not be read: {error}") from error

    orphans = [key for key in candidates if key not in linked]
    report.linked = len(candidates) - len(orphans)
    report.orphans = len(orphans)
    to_delete, report.remaining = orphans[:batch], max(0, len(orphans) - batch)

    # 3. Delete, one file at a time; a failure is reported and left for the next run.
    for key in to_delete:
        if dry_run:
            report.deleted.append(key)
            continue
        try:
            store.delete(key)
        except (StorageUnavailable, OSError) as error:
            # Both storages raise StorageUnavailable; OSError is caught too, so a storage that
            # lets a disk error through still cannot stop the rest of the batch.
            # The key is not logged: it carries the user id. The count is in the summary.
            log.warning("an orphan file could not be deleted, will retry next run (%s)", type(error).__name__)
            report.failed.append(key)
        else:
            report.deleted.append(key)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.orphans", description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="report what would be deleted, delete nothing")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH, help="most files to delete in this run")
    parser.add_argument("--min-age-minutes", type=int, default=int(DEFAULT_MIN_AGE.total_seconds() // 60),
                        help="leave files younger than this alone (uploads in progress)")
    args = parser.parse_args(argv)
    if args.batch < 1 or args.min_age_minutes < 1:
        parser.error("--batch and --min-age-minutes must be at least 1")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        report = sweep(batch=args.batch, min_age=timedelta(minutes=args.min_age_minutes), dry_run=args.dry_run)
    except InventoryFailed as error:
        log.error("stopped without deleting anything: %s", error)
        return 2
    log.info(report.summary())
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
