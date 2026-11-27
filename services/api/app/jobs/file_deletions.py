"""Retry the photo files the server could not delete (plan 4.10.3, layer 2; notice #59).

    python -m app.jobs.file_deletions [--dry-run]

NOT SCHEDULED ANYWHERE (Ali, 2026-11-26). Until it runs on a schedule (the plan says every 10
minutes), nothing processes the queue on its own: queued files stay in storage, and the orphan
job (`python -m app.jobs.orphans`) is the only other way they are removed. Reconciliation is not
to be enabled until the queue job is scheduled, or the orphan job is.

What it may do, and nothing more:
- connect to PostgreSQL as dolab_file_cleaner, whose only table is dolab.pending_file_deletions
  (SELECT and DELETE; UPDATE on attempts, next_try_at and last_error only) and which runs no
  function;
- delete objects in the photo bucket (production: an identity that may only DeleteObject there).
  It never reads, lists or writes files. It refuses to start if a signing key or the API's JWT
  secret is in its environment, or if its bucket is the ledger bucket.

A run: lease due rows in batches, then delete each file (a file already gone counts as deleted)
and remove its row, or reschedule it with a growing wait (10 minutes, doubling, at most 6 hours)
and the error's class name. A batch in which every file failed in storage means storage is
down: the run stops. Then it counts rows queued for more than 24 hours and alerts on them.

Invalid keys (review of #60): a row whose key is not a photo key the server makes
("<user id>/<photo id>_original.<ext>" or "_thumb.jpg") is never sent to storage, so nothing
outside DOLAB's photo files can be deleted. It is not a storage failure and does not stop the
batch: it stays queued with last_error "invalid_key", is put back the longest wait (6 hours),
and makes the run exit 1 so someone looks at it. It is never dropped.

Leasing (review of #60, M1): one short statement takes a batch of due rows (FOR UPDATE SKIP
LOCKED, so two runs never take the same row) and moves their next_try_at LEASE ahead, and commits.
No transaction stays open and no row lock is held while files are deleted, so the server's own
removal of queue rows never waits on the job. A run that stops part-way leaves its leased rows
to come due again when the lease ends; deleting a file twice is harmless. Rows are first due
five minutes after the deletion, so the job never takes one the server is still working on.
Safe to run again or twice at once.

--dry-run changes nothing: it counts the rows due and the rows older than 24 hours.
Logs carry counts only, never keys (a key carries the user id).

Exit codes: 0 done; 1 some files failed, a key was invalid, or a row is older than 24 hours
(alert); 4 refused to start (configuration); 5 the database could not be reached (wrong
credentials, unknown database, no server, connection limit reached): nothing was read or
changed. Only the error's class is logged, never the connection string.
"""
import argparse
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from ..storage_keys import check_key, is_photo_key

log = logging.getLogger("dolab.jobs.file_deletions")

ROLE = "dolab_file_cleaner"
BATCH = 100
MAX_BATCHES = 50  # at most 5000 files a run; the next run takes the rest
FIRST_WAIT = 600  # seconds
MAX_WAIT = 6 * 3600
LEASE = 30 * 60  # seconds a leased batch is kept from other runs
STALE_HOURS = 24
ENVIRONMENTS = ("development", "test", "staging", "production")
EXIT_OK, EXIT_PROBLEMS, EXIT_REFUSED, EXIT_NO_DATABASE = 0, 1, 4, 5
CONNECT_TIMEOUT = 10  # seconds


class Refused(RuntimeError):
    """The job will not start with this configuration."""


def wait_after(attempts: int) -> int:
    """Seconds before the next try of a file that has now failed `attempts + 1` times."""
    return min(FIRST_WAIT * 2 ** min(attempts, 16), MAX_WAIT)


# ------------------------------------------------------------------ configuration

@dataclass(frozen=True)
class Config:
    environment: str
    database_url: str
    storage: str  # "s3" or "local"
    bucket: str
    media_dir: str
    endpoint_url: str | None
    region: str


def load_config(env: dict[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    environment = env.get("DOLAB_ENV", "development")
    if environment not in ENVIRONMENTS:
        raise Refused(f"DOLAB_ENV must be one of {', '.join(ENVIRONMENTS)}")
    strict = environment in ("staging", "production")
    for secret in ("DOLAB_LEDGER_SIGNING_KEY", "DOLAB_JWT_SECRET"):
        if env.get(secret):
            raise Refused(f"{secret} must not be set for the file deletion job")
    database_url = env.get("DOLAB_FILE_CLEANER_DATABASE_URL", "")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise Refused("DOLAB_FILE_CLEANER_DATABASE_URL must be set to a PostgreSQL URL for dolab_file_cleaner")
    storage = env.get("DOLAB_STORAGE", "local")
    if storage not in ("local", "s3"):
        raise Refused("DOLAB_STORAGE must be 'local' or 's3'")
    if strict and storage != "s3":
        raise Refused(f"DOLAB_STORAGE must be 's3' in {environment}")
    bucket = env.get("DOLAB_S3_BUCKET", "")
    if storage == "s3" and not bucket:
        raise Refused("DOLAB_S3_BUCKET must be set when DOLAB_STORAGE is 's3'")
    if bucket and bucket == env.get("DOLAB_LEDGER_BUCKET"):
        raise Refused("DOLAB_S3_BUCKET is the ledger bucket; the job deletes photo files only")
    endpoint_url = env.get("DOLAB_S3_ENDPOINT_URL") or None
    if strict and endpoint_url:
        parsed = urlparse(endpoint_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise Refused(f"DOLAB_S3_ENDPOINT_URL must use https:// in {environment}")
    return Config(environment=environment, database_url=database_url, storage=storage, bucket=bucket,
                  media_dir=env.get("DOLAB_MEDIA_DIR", "./media"), endpoint_url=endpoint_url,
                  region=env.get("DOLAB_S3_REGION", "us-east-1"))


# ------------------------------------------------------------------ deleting files

class FileDeleter(Protocol):
    """Delete one stored file. A file already gone is not an error. Nothing else."""

    def delete(self, key: str) -> None: ...


class LocalFileDeleter:
    """The media folder (development and tests)."""

    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()

    def delete(self, key: str) -> None:
        path = (self.root / check_key(key)).resolve()
        if self.root not in path.parents:
            raise ValueError("invalid storage key")
        path.unlink(missing_ok=True)


class S3FileDeleter:
    """The photo bucket. Calls delete_object only."""

    def __init__(self, bucket: str, client) -> None:
        self.bucket, self._client = bucket, client

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self.bucket, Key=check_key(key))
        except Exception as error:  # noqa: BLE001
            code = str(getattr(error, "response", {}).get("Error", {}).get("Code", ""))
            if code in ("NoSuchKey", "404", "NotFound"):
                return
            raise


def build_deleter(config: Config) -> FileDeleter:
    if config.storage == "s3":
        import boto3.session
        from botocore.config import Config as BotoConfig

        client = boto3.session.Session().client(
            "s3", endpoint_url=config.endpoint_url, region_name=config.region,
            config=BotoConfig(connect_timeout=5, read_timeout=10, retries={"mode": "standard", "max_attempts": 2},
                              s3={"addressing_style": "path"} if config.endpoint_url else {}),
        )
        return S3FileDeleter(config.bucket, client)
    return LocalFileDeleter(config.media_dir)


# ------------------------------------------------------------------ the queue

class Queue(Protocol):
    def claim(self, limit: int) -> list[tuple[str, int]]: ...

    def done(self, key: str) -> None: ...

    def failed(self, key: str, attempts: int, error: str, wait: int | None = None) -> None: ...

    def due(self) -> int: ...

    def stale(self) -> int: ...


class PgQueue:
    """dolab_file_cleaner's connection: pending_file_deletions, nothing else."""

    def __init__(self, url: str) -> None:
        import psycopg

        url = url.replace("postgresql+psycopg://", "postgresql://", 1)
        # Every statement commits on its own: no transaction stays open while files are deleted.
        self.conn = psycopg.connect(url, autocommit=True, application_name="dolab-file-deletions",
                                    connect_timeout=CONNECT_TIMEOUT)
        current, session = self.conn.execute("SELECT current_user, session_user").fetchone()
        if current != ROLE or session != ROLE:
            self.conn.close()
            raise Refused(f"the job must connect as {ROLE}, not {session}")

    def claim(self, limit: int) -> list[tuple[str, int]]:
        """Lease up to `limit` due rows: one statement, committed at once."""
        rows = self.conn.execute(
            "UPDATE dolab.pending_file_deletions q SET next_try_at = now() + make_interval(secs => %s) "
            "FROM (SELECT key FROM dolab.pending_file_deletions WHERE next_try_at <= now() "
            "      ORDER BY next_try_at, key LIMIT %s FOR UPDATE SKIP LOCKED) due "
            "WHERE q.key = due.key RETURNING q.key, q.attempts", (LEASE, limit)).fetchall()
        return sorted(rows)

    def done(self, key: str) -> None:
        self.conn.execute("DELETE FROM dolab.pending_file_deletions WHERE key = %s", (key,))

    def failed(self, key: str, attempts: int, error: str, wait: int | None = None) -> None:
        self.conn.execute(
            "UPDATE dolab.pending_file_deletions SET attempts = attempts + 1, "
            "next_try_at = now() + make_interval(secs => %s), last_error = %s WHERE key = %s",
            (wait_after(attempts) if wait is None else wait, error[:40], key))

    def due(self) -> int:
        return self.conn.execute(
            "SELECT count(*) FROM dolab.pending_file_deletions WHERE next_try_at <= now()").fetchone()[0]

    def stale(self) -> int:
        return self.conn.execute(
            "SELECT count(*) FROM dolab.pending_file_deletions WHERE created_at < now() - make_interval(hours => %s)",
            (STALE_HOURS,)).fetchone()[0]

    def close(self) -> None:
        self.conn.close()


# ------------------------------------------------------------------ a run

@dataclass
class Report:
    dry_run: bool = False
    claimed: int = 0
    deleted: int = 0
    failed: int = 0  # storage failures
    invalid: int = 0  # keys that are not photo keys: never sent to storage
    stopped_early: bool = False  # a whole batch failed: storage looks down
    due: int | None = None  # dry run only
    stale: int = 0

    @property
    def clean(self) -> bool:
        return self.failed == 0 and self.invalid == 0 and self.stale == 0

    def summary(self) -> str:
        if self.dry_run:
            return f"dry run: {self.due} files due, {self.stale} queued for more than {STALE_HOURS} hours"
        return (f"claimed {self.claimed}, deleted {self.deleted}, failed {self.failed}, invalid keys {self.invalid}"
                f"{' (stopped: storage looks down)' if self.stopped_early else ''}; "
                f"{self.stale} queued for more than {STALE_HOURS} hours")


def run(queue: Queue, deleter: FileDeleter, *, dry_run: bool = False, batch: int = BATCH,
        max_batches: int = MAX_BATCHES) -> Report:
    report = Report(dry_run=dry_run)
    if dry_run:
        report.due, report.stale = queue.due(), queue.stale()
        return _finish(report)
    for _ in range(max_batches):
        rows = queue.claim(batch)
        if not rows:
            break
        report.claimed += len(rows)
        tried = failed_here = 0
        for key, attempts in rows:
            if not is_photo_key(key):
                # Never sent to storage; kept, set aside for the longest wait, and reported.
                queue.failed(key, attempts, "invalid_key", wait=MAX_WAIT)
                report.invalid += 1
                continue
            tried += 1
            try:
                deleter.delete(key)
            except Exception as error:  # noqa: BLE001 - one file must not stop the others
                queue.failed(key, attempts, "invalid_key" if isinstance(error, ValueError) else type(error).__name__)
                failed_here += 1
                continue
            queue.done(key)
            report.deleted += 1
        report.failed += failed_here
        if tried and failed_here == tried:
            report.stopped_early = True  # every file sent to storage failed: storage is down
            break
    report.stale = queue.stale()
    return _finish(report)


def _finish(report: Report) -> Report:
    if report.failed:
        log.error("ALERT %d photo files could not be deleted; rescheduled", report.failed)
    if report.invalid:
        log.error("ALERT %d queued keys are not photo keys; not deleted, kept in the queue", report.invalid)
    if report.stale:
        log.error("ALERT %d photo files queued for more than %d hours", report.stale, STALE_HOURS)
    log.info(report.summary())
    return report


def exit_code(report: Report) -> int:
    return EXIT_OK if report.clean else EXIT_PROBLEMS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.file_deletions", description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="change nothing; count what is due")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        config = load_config()
        queue = PgQueue(config.database_url)
    except Refused as error:
        log.error("refused to start: %s", error)
        return EXIT_REFUSED
    except Exception as error:  # noqa: BLE001 - the connection failed: nothing read, nothing changed
        # Only the class: a driver's message can carry the host, user and database names.
        log.error("ALERT could not connect to the database (%s); nothing done", type(error).__name__)
        return EXIT_NO_DATABASE
    try:
        return exit_code(run(queue, build_deleter(config), dry_run=args.dry_run))
    except Exception as error:  # noqa: BLE001 - leased rows come due again when their lease ends
        log.error("ALERT the run stopped (%s)", type(error).__name__)
        return EXIT_PROBLEMS
    finally:
        queue.close()


if __name__ == "__main__":
    sys.exit(main())
