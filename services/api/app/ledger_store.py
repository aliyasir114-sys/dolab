"""Writing deletion ledger entries (plan 4.10.1).

The server may only add entries: on S3 it needs PutObject on the ledger bucket and nothing else
(no read, list or delete), and every write is conditional on the object not existing yet
(If-None-Match: *), so an entry is never replaced; in staging and production the bucket also has
Object Lock in compliance mode (set up with the provider, step H). In development and tests the
"local" ledger is a folder, written the same way (create-only files).

write_entry tries at most three times within two seconds (plan 4.10.1, step 2), then gives up:
the caller must then delete nothing.
"""
import logging
import os
import time
from pathlib import Path
from typing import Protocol

from .config import Settings, settings
from .ledger import Entry

log = logging.getLogger("dolab.ledger")

ATTEMPTS = 3
BUDGET_SECONDS = 2.0
# Leave room for non-socket work between the bounded S3 call and the deadline.
DEADLINE_SAFETY_SECONDS = 0.05
CONNECT_TIMEOUT_SECONDS = 0.5
READ_TIMEOUT_SECONDS = 1.0


class LedgerUnavailable(RuntimeError):
    """The entry could not be written. Nothing may be deleted."""


class LedgerEntryExists(LedgerUnavailable):
    """An entry with this name is already there: it is never replaced, and never retried."""


class LedgerWriter(Protocol):
    def put(self, name: str, body: bytes) -> None: ...


class LocalLedger:
    """A folder of create-only files: development and tests."""

    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def put(self, name: str, body: bytes) -> None:
        path = self.root / name
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as error:
            raise LedgerEntryExists(f"entry already exists: {name}") from error
        except OSError as error:
            raise LedgerUnavailable(f"ledger folder unavailable: {type(error).__name__}") from error
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())  # confirmed means on disk
        except OSError as error:
            path.unlink(missing_ok=True)
            raise LedgerUnavailable(f"ledger write failed: {type(error).__name__}") from error


# Socket timeouts (connect, read) of the ledger's S3 clients, largest first. Each attempt uses the
# largest whose total fits in the time it has left, so a write never outlasts its budget.
TIERS = ((CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS), (0.3, 0.6), (0.15, 0.3), (0.05, 0.1))


def _make_client(endpoint_url: str | None, region: str, connect: float, read: float):
    """One S3 client from a session of its own. boto3.client() would use the shared default
    session, which is not safe to use from several threads at once; the session made here is
    used only to make this client, in one thread at start-up, and never shared. The client
    itself is safe to share between threads (Boto3 guide, "Clients")."""
    import boto3.session
    from botocore.config import Config

    return boto3.session.Session().client(
        "s3", endpoint_url=endpoint_url, region_name=region,
        config=Config(connect_timeout=connect, read_timeout=read, retries={"total_max_attempts": 1},
                      s3={"addressing_style": "path"} if endpoint_url else {}),
    )


class S3Ledger:
    """The ledger bucket. Its clients are made once, at start-up, one per timeout tier, and then
    shared by every request thread: no client or session is made while a deletion waits.
    No client retries: write_entry keeps the budget."""

    def __init__(self, bucket: str, *, endpoint_url: str | None = None, region: str = "us-east-1",
                 encryption: str | None = "AES256", clients=None) -> None:
        self.bucket, self.encryption = bucket, encryption
        # [(connect + read seconds, client)], largest first. Tests may pass their own.
        self.clients = clients if clients is not None else [
            (connect + read, _make_client(endpoint_url, region, connect, read)) for connect, read in TIERS
        ]

    def put(self, name: str, body: bytes) -> None:
        self.put_with_timeout(name, body, self.clients[0][0])

    def put_with_timeout(self, name: str, body: bytes, timeout: float) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        client = next((c for total, c in self.clients if total <= timeout), None)
        if client is None:
            raise LedgerUnavailable("ledger write deadline exceeded")
        extra = {"ServerSideEncryption": self.encryption} if self.encryption else {}
        try:
            client.put_object(Bucket=self.bucket, Key=name, Body=body, ContentType="application/json",
                              IfNoneMatch="*", **extra)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in ("PreconditionFailed", "412", "ConditionalRequestConflict"):
                raise LedgerEntryExists(f"entry already exists: {name}") from error
            raise LedgerUnavailable(f"ledger write failed: {code}") from error
        except BotoCoreError as error:
            raise LedgerUnavailable(f"ledger write failed: {type(error).__name__}") from error


def write_entry(writer: LedgerWriter, entry: Entry, *, attempts: int = ATTEMPTS,
                budget: float = BUDGET_SECONDS) -> None:
    """Write one entry and wait for the confirmation; raises LedgerUnavailable if it cannot."""
    deadline = time.monotonic() + budget
    body, name = entry.to_json(), entry.object_name
    last_error = None
    for attempt in range(1, attempts + 1):
        remaining = deadline - time.monotonic()
        timeout = remaining - DEADLINE_SAFETY_SECONDS
        if timeout <= 0:
            error = last_error or LedgerUnavailable("ledger write deadline exceeded")
            log.error("ALERT deletion ledger deadline exceeded after %d attempt(s): %s", attempt - 1, error)
            raise error
        try:
            put_with_timeout = getattr(writer, "put_with_timeout", None)
            if put_with_timeout:
                put_with_timeout(name, body, timeout)
            else:
                writer.put(name, body)
            return
        except LedgerEntryExists:
            log.error("ALERT deletion ledger entry already exists: %s", name)
            raise  # never retried: an entry is written once
        except LedgerUnavailable as error:
            last_error = error
            left = deadline - time.monotonic()
            pause = 0.1 * attempt
            if attempt == attempts or left <= pause + DEADLINE_SAFETY_SECONDS:
                log.error("ALERT deletion ledger unavailable after %d attempt(s): %s", attempt, error)
                raise
            log.warning("deletion ledger write failed (attempt %d): %s", attempt, error)
            time.sleep(pause)


def build_writer(config: Settings) -> LedgerWriter:
    if config.ledger_storage == "s3":
        return S3Ledger(config.ledger_bucket, endpoint_url=config.s3_endpoint_url, region=config.s3_region,
                        encryption=config.s3_encryption)
    return LocalLedger(config.ledger_dir)


writer: LedgerWriter = build_writer(settings)
