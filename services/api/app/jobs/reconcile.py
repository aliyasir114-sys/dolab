"""Complete deletions the server recorded in the ledger but could not finish (plan 4.10.1ب; notice #57).

    python -m app.jobs.reconcile [--dry-run]

NOT SCHEDULED OR ENABLED ANYWHERE (Ali, 2026-11-26). Before it runs on a schedule in any
environment, `python -m app.jobs.orphans` must already run there on a schedule, or the
pending_file_deletions queue must exist: this job deletes database rows only, and the photo files
of the deletions it completes are removed by the orphan job alone.

What it may do, and nothing more:
- read the deletion ledger (list and get; it has no way to write or delete there) with the
  Ed25519 public keys only. It refuses to start if a signing key is in its environment;
- connect to PostgreSQL as dolab_reconciler, which has no privilege on any table and may only run
  dolab.reconcile_apply, dolab.reconcile_purge_intents and dolab.reconcile_record_run.

A run, in this order (Ali, 2026-11-26):
1. Take the run lock (a PostgreSQL advisory lock). If another run holds it, stop: exit 3.
2. List the ledger for the last 31 days in full. If the listing fails, record the run as failed
   and stop: exit 2. Nothing is deleted.
3. Read and verify every entry (ledger-entry-v1: format, object name, known key, signature, age).
   An invalid entry is counted and never acted on. An entry older than 31 days is skipped.
4. Call reconcile_apply for each valid entry. One failure is counted and the others go on.
5. Only if every entry was valid and every call succeeded: purge intents older than 31 days.
6. Record the run (counts only). It is recorded as failed if any entry was invalid or any error
   happened. Exit 1 in that case, 0 otherwise.

--dry-run changes nothing: reconcile_apply and the purge are asked what they would do, and the run
is not recorded. Logs carry counts, outcomes and entry ids only; never user or entity ids.

Exit codes: 0 done; 1 done with invalid entries or errors; 2 ledger listing failed; 3 another run
is in progress; 4 refused to start (configuration).
"""
import argparse
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from .. import ledger

log = logging.getLogger("dolab.jobs.reconcile")

ROLE = "dolab_reconciler"
WINDOW = timedelta(days=31)
# A deleted_at further ahead than this is not a clock difference: the entry is refused.
FUTURE_TOLERANCE = timedelta(minutes=10)
# pg_try_advisory_lock key for "a reconciliation run is in progress" ("DOLABRC" in ASCII).
LOCK_KEY = 0x444F4C41425243
OUTCOMES = ("deleted", "already_done", "absent", "no_intent", "owner_mismatch", "would_delete")
ENVIRONMENTS = ("development", "test", "staging", "production")
EXIT_OK, EXIT_PROBLEMS, EXIT_LISTING_FAILED, EXIT_LOCKED, EXIT_REFUSED = 0, 1, 2, 3, 4


class ListingFailed(RuntimeError):
    """The ledger could not be listed in full."""


class Refused(RuntimeError):
    """The job will not start with this configuration."""


# ------------------------------------------------------------------ configuration

@dataclass(frozen=True)
class Config:
    environment: str
    database_url: str
    public_keys: dict
    storage: str  # "s3" or "local"
    bucket: str
    directory: str
    endpoint_url: str | None
    region: str


def load_config(env: dict[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    environment = env.get("DOLAB_ENV", "development")
    if environment not in ENVIRONMENTS:
        raise Refused(f"DOLAB_ENV must be one of {', '.join(ENVIRONMENTS)}")
    strict = environment in ("staging", "production")
    # The private key never reaches this job: its presence is a deployment mistake.
    if env.get("DOLAB_LEDGER_SIGNING_KEY"):
        raise Refused("DOLAB_LEDGER_SIGNING_KEY must not be set for the reconciliation job")
    database_url = env.get("DOLAB_RECONCILER_DATABASE_URL", "")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise Refused("DOLAB_RECONCILER_DATABASE_URL must be set to a PostgreSQL URL for dolab_reconciler")
    spec = env.get("DOLAB_LEDGER_PUBLIC_KEYS", "")
    if not spec:
        if strict:
            raise Refused(f"DOLAB_LEDGER_PUBLIC_KEYS must be set in {environment}")
        spec = ",".join(f"{key_id}:{text}" for key_id, text in ledger.TEST_PUBLIC_KEYS.items())
    try:
        public_keys = ledger.public_keys(spec)
    except ledger.LedgerError as error:
        raise Refused(f"DOLAB_LEDGER_PUBLIC_KEYS refused: {error.code}") from None
    if not public_keys:
        raise Refused("DOLAB_LEDGER_PUBLIC_KEYS lists no key")
    if strict:
        test_keys = set(ledger.TEST_PUBLIC_KEYS.values())
        for key_id, key in public_keys.items():
            if ledger._b64url(key.public_bytes_raw()) in test_keys:
                raise Refused(f"the published test key {key_id!r} cannot be trusted in {environment}")
    storage = env.get("DOLAB_LEDGER_STORAGE", "s3" if strict else "local")
    if storage not in ("local", "s3"):
        raise Refused("DOLAB_LEDGER_STORAGE must be 'local' or 's3'")
    if strict and storage != "s3":
        raise Refused(f"DOLAB_LEDGER_STORAGE must be 's3' in {environment}")
    bucket = env.get("DOLAB_LEDGER_BUCKET", "")
    if storage == "s3" and not bucket:
        raise Refused("DOLAB_LEDGER_BUCKET must be set when DOLAB_LEDGER_STORAGE is 's3'")
    endpoint_url = env.get("DOLAB_S3_ENDPOINT_URL") or None
    if strict and endpoint_url:
        parsed = urlparse(endpoint_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise Refused(f"DOLAB_S3_ENDPOINT_URL must use https:// in {environment}")
    return Config(environment=environment, database_url=database_url, public_keys=public_keys, storage=storage,
                  bucket=bucket, directory=env.get("DOLAB_LEDGER_DIR", "./ledger"), endpoint_url=endpoint_url,
                  region=env.get("DOLAB_S3_REGION", "us-east-1"))


# ------------------------------------------------------------------ reading the ledger

class LedgerReader(Protocol):
    """Read-only: list names under a prefix, get one entry's bytes. Nothing else."""

    def list_names(self, prefix: str) -> list[str]: ...

    def get(self, name: str) -> bytes: ...


class LocalLedgerReader:
    """The ledger folder (development and tests)."""

    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def list_names(self, prefix: str) -> list[str]:
        folder = self.root / prefix
        try:
            if not folder.exists():
                return []
            return sorted(prefix + child.name for child in folder.iterdir())
        except OSError as error:
            raise ListingFailed(f"ledger folder: {type(error).__name__}") from error

    def get(self, name: str) -> bytes:
        # Never more than one byte past the limit: a larger entry is refused as too large.
        fd = os.open(self.root / name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as file:
            return file.read(ledger.MAX_ENTRY_BYTES + 1)


class S3LedgerReader:
    """The ledger bucket. Calls list_objects_v2 and get_object only; the credentials it is given
    must allow nothing more (production: a read-only identity for the ledger bucket)."""

    def __init__(self, bucket: str, client) -> None:
        self.bucket, self._client = bucket, client

    def list_names(self, prefix: str) -> list[str]:
        names: list[str] = []
        kwargs = {"Bucket": self.bucket, "Prefix": prefix}
        try:
            while True:
                page = self._client.list_objects_v2(**kwargs)
                names.extend(item["Key"] for item in page.get("Contents", []))
                if not page.get("IsTruncated"):
                    return names
                kwargs["ContinuationToken"] = page["NextContinuationToken"]
        except Exception as error:  # noqa: BLE001 - any failure means the listing is not complete
            raise ListingFailed(f"ledger bucket: {type(error).__name__}") from error

    def get(self, name: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=name,
                                               Range=f"bytes=0-{ledger.MAX_ENTRY_BYTES}")
        except Exception as error:  # noqa: BLE001
            code = getattr(error, "response", {}).get("Error", {}).get("Code")
            if code == "InvalidRange":  # an empty object: refused like any malformed entry
                return b""
            raise
        return response["Body"].read(ledger.MAX_ENTRY_BYTES + 1)


def s3_client(endpoint_url: str | None, region: str):
    import boto3.session
    from botocore.config import Config as BotoConfig

    return boto3.session.Session().client(
        "s3", endpoint_url=endpoint_url, region_name=region,
        config=BotoConfig(connect_timeout=5, read_timeout=10, retries={"mode": "standard", "max_attempts": 3},
                          s3={"addressing_style": "path"} if endpoint_url else {}),
    )


def build_reader(config: Config) -> LedgerReader:
    if config.storage == "s3":
        return S3LedgerReader(config.bucket, s3_client(config.endpoint_url, config.region))
    return LocalLedgerReader(config.directory)


def day_prefixes(now: datetime) -> list[str]:
    """YYYY/MM/DD/ for every day an entry younger than 31 days can carry."""
    first: date = (now - WINDOW).date()
    return [(first + timedelta(days=n)).strftime("%Y/%m/%d/") for n in range((now.date() - first).days + 1)]


def entry_time(entry: ledger.Entry) -> datetime:
    return datetime.strptime(entry.deleted_at, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ the database

class Database(Protocol):
    def lock(self) -> bool: ...

    def reconcile(self, entry: ledger.Entry, dry_run: bool) -> str: ...

    def purge(self, dry_run: bool) -> int: ...

    def record(self, report: "Report") -> None: ...


class PgDatabase:
    """dolab_reconciler's connection: the three functions and the run lock, nothing else."""

    def __init__(self, url: str) -> None:
        import psycopg

        url = url.replace("postgresql+psycopg://", "postgresql://", 1)
        self.conn = psycopg.connect(url, autocommit=True, application_name="dolab-reconcile")
        current, session = self.conn.execute("SELECT current_user, session_user").fetchone()
        if current != ROLE or session != ROLE:
            self.conn.close()
            raise Refused(f"the job must connect as {ROLE}, not {session}")

    def lock(self) -> bool:
        return self.conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()[0]

    def reconcile(self, entry: ledger.Entry, dry_run: bool) -> str:
        return self.conn.execute(
            "SELECT dolab.reconcile_apply(%s::uuid, %s, %s::uuid, %s::uuid, %s)",
            (entry.entry_id, entry.entity_type, entry.entity_id, entry.user_id, dry_run),
        ).fetchone()[0]

    def purge(self, dry_run: bool) -> int:
        return self.conn.execute("SELECT dolab.reconcile_purge_intents(%s)", (dry_run,)).fetchone()[0]

    def record(self, report: "Report") -> None:
        c = report.outcomes
        self.conn.execute(
            "SELECT dolab.reconcile_record_run(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (report.started, report.finished, report.read, report.valid, report.invalid, c["deleted"],
             c["already_done"], c["absent"], c["no_intent"], c["owner_mismatch"], report.errors),
        )

    def close(self) -> None:
        self.conn.close()  # also releases the run lock


# ------------------------------------------------------------------ a run

@dataclass
class Report:
    started: datetime
    finished: datetime | None = None
    dry_run: bool = False
    listing_failed: bool = False
    read: int = 0  # entries listed
    valid: int = 0  # verified and inside the window
    invalid: int = 0  # refused by the verification (any rule); never acted on
    too_old: int = 0  # valid signature or not, older than 31 days: skipped
    errors: int = 0  # read or reconcile_apply failures, and a failed listing
    outcomes: dict[str, int] = field(default_factory=lambda: dict.fromkeys(OUTCOMES, 0))
    purged: int | None = None  # None: the purge did not run

    @property
    def clean(self) -> bool:
        return not self.listing_failed and self.invalid == 0 and self.errors == 0

    def summary(self) -> str:
        outcomes = ", ".join(f"{name} {count}" for name, count in self.outcomes.items())
        purge = "skipped" if self.purged is None else (f"would purge {self.purged}" if self.dry_run
                                                        else f"purged {self.purged}")
        return (f"{'dry run: ' if self.dry_run else ''}read {self.read}, valid {self.valid}, invalid {self.invalid}, "
                f"too old {self.too_old}, errors {self.errors}; {outcomes}; intents {purge}"
                f"{'; LEDGER LISTING FAILED' if self.listing_failed else ''}")


def run(reader: LedgerReader, db: Database, public_keys: dict, *, dry_run: bool = False,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> Report:
    report = Report(started=now(), dry_run=dry_run)
    # 2. The full listing first.
    names: list[str] = []
    try:
        for prefix in day_prefixes(report.started):
            names.extend(reader.list_names(prefix))
    except ListingFailed as error:
        log.error("ALERT ledger listing failed, nothing processed: %s", error)
        report.listing_failed, report.errors = True, 1
        return _finish(report, db, now)
    report.read = len(names)
    oldest, newest = report.started - WINDOW, report.started + FUTURE_TOLERANCE
    for name in names:
        # 3. Verify.
        try:
            raw = reader.get(name)
        except Exception as error:  # noqa: BLE001
            log.error("ledger entry could not be read: %s (%s)", name, type(error).__name__)
            report.errors += 1
            continue
        try:
            entry = ledger.verify_entry(raw, name, public_keys)
            moment = entry_time(entry)
            if moment > newest:
                raise ledger.LedgerError("future", "deleted_at is in the future")
        except ledger.LedgerError as error:
            log.error("ALERT invalid ledger entry, not acted on: %s (%s)", name, error.code)
            report.invalid += 1
            continue
        if moment < oldest:
            report.too_old += 1
            continue
        report.valid += 1
        # 4. Complete it, if the server left it half done.
        try:
            outcome = db.reconcile(entry, dry_run)
        except Exception as error:  # noqa: BLE001 - one failure must not stop the others
            log.error("reconcile_apply failed for entry %s (%s)", entry.entry_id, type(error).__name__)
            report.errors += 1
            continue
        if outcome not in OUTCOMES:
            log.error("reconcile_apply returned an unknown outcome for entry %s", entry.entry_id)
            report.errors += 1
            continue
        report.outcomes[outcome] += 1
        if outcome == "owner_mismatch":
            log.error("ALERT entry %s names an entity its user does not own; nothing deleted", entry.entry_id)
        elif outcome != "already_done":
            log.info("entry %s: %s", entry.entry_id, outcome)
    # 5. Purge old intents only after a clean, complete run.
    if report.clean:
        try:
            report.purged = db.purge(dry_run)
        except Exception as error:  # noqa: BLE001
            log.error("intent purge failed (%s)", type(error).__name__)
            report.errors += 1
    else:
        log.warning("intent purge skipped: the run had invalid entries or errors")
    return _finish(report, db, now)


def _finish(report: Report, db: Database, now: Callable[[], datetime]) -> Report:
    report.finished = now()
    if not report.dry_run:
        # 6. Record it; as failed if anything was invalid or went wrong.
        try:
            db.record(report)
        except Exception as error:  # noqa: BLE001
            log.error("ALERT the run could not be recorded (%s)", type(error).__name__)
            report.errors += 1
    log.info(report.summary())
    return report


def exit_code(report: Report) -> int:
    if report.listing_failed:
        return EXIT_LISTING_FAILED
    return EXIT_OK if report.clean else EXIT_PROBLEMS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.reconcile", description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="change nothing; report what would be done")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        config = load_config()
        db = PgDatabase(config.database_url)
    except Refused as error:
        log.error("refused to start: %s", error)
        return EXIT_REFUSED
    try:
        if not db.lock():
            log.warning("another reconciliation run is in progress; nothing done")
            return EXIT_LOCKED
        return exit_code(run(build_reader(config), db, config.public_keys, dry_run=args.dry_run))
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
