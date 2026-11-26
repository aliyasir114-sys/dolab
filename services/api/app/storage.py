"""Where photo files live (plan 4.6).

One interface, two implementations, chosen by DOLAB_STORAGE:
- "local": a folder on disk (DOLAB_MEDIA_DIR). Development and tests only.
- "s3": any S3-compatible object storage (MinIO in CI; the provider is chosen in step H). The
  bucket is private and files are encrypted at rest by the provider (DOLAB_S3_ENCRYPTION).
  Credentials come from the standard AWS variables or the platform's role, never from here.

Photos are still served by the API through its own short-lived signed links, not by storage
links, which cannot be revoked when a photo is deleted (decision 19). Uploads still pass
through the API, which checks the file and strips its metadata first.
"""
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .config import Settings, settings
from .storage_keys import StorageUnavailable, check_key  # noqa: F401 - re-exported

log = logging.getLogger("dolab.storage")



# Large reads (the ZIP export) go through read_chunks in blocks of this size, never whole.
CHUNK = 1024 * 1024


@dataclass(frozen=True)
class StoredObject:
    key: str
    modified: datetime  # timezone-aware, UTC


class Storage(Protocol):
    def save(self, key: str, data: bytes) -> None: ...
    def read(self, key: str) -> bytes | None: ...
    def read_chunks(self, key: str, chunk_size: int = CHUNK) -> Iterator[bytes] | None: ...
    def delete(self, key: str) -> None: ...
    def list_keys(self, prefix: str) -> list[str]: ...
    def list_objects(self, prefix: str) -> list[StoredObject]: ...


class LocalStorage:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / check_key(key)).resolve()
        # Never let a key escape the media folder.
        if self.root not in path.parents:
            raise ValueError("invalid storage key")
        return path

    # Disk errors (permissions, a full or failing disk) are StorageUnavailable, as on S3, so every
    # caller handles one kind of storage failure.

    def save(self, key: str, data: bytes) -> None:
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as error:
            raise StorageUnavailable(f"storage save failed: {type(error).__name__}") from error

    def read(self, key: str) -> bytes | None:
        path = self._path(key)
        try:
            return path.read_bytes() if path.is_file() else None
        except OSError as error:
            raise StorageUnavailable(f"storage read failed: {type(error).__name__}") from error

    def read_chunks(self, key: str, chunk_size: int = CHUNK) -> Iterator[bytes] | None:
        """The file in blocks, or None if it does not exist. A failure part-way raises StorageUnavailable."""
        path = self._path(key)
        try:
            handle = path.open("rb")
        except FileNotFoundError:
            return None
        except OSError as error:
            raise StorageUnavailable(f"storage read failed: {type(error).__name__}") from error

        def blocks() -> Iterator[bytes]:
            with handle:
                while True:
                    try:
                        block = handle.read(chunk_size)
                    except OSError as error:
                        raise StorageUnavailable(f"storage read failed: {type(error).__name__}") from error
                    if not block:
                        return
                    yield block

        return blocks()

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError as error:
            raise StorageUnavailable(f"storage delete failed: {type(error).__name__}") from error

    def list_objects(self, prefix: str) -> list[StoredObject]:
        """Every file whose key starts with prefix, sorted by key, with its modification time."""
        if not self.root.exists():
            return []
        found = []
        for path in self.root.rglob("*"):
            if path.is_file():
                key = path.relative_to(self.root).as_posix()
                if key.startswith(prefix):
                    found.append(StoredObject(key, datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)))
        return sorted(found, key=lambda o: o.key)

    def list_keys(self, prefix: str) -> list[str]:
        return [o.key for o in self.list_objects(prefix)]


class S3Storage:
    """S3-compatible object storage. Missing objects read as None and delete without error."""

    def __init__(self, bucket: str, *, endpoint_url: str | None = None, region: str = "us-east-1",
                 encryption: str | None = "AES256", client=None) -> None:
        self.bucket = bucket
        self.encryption = encryption
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                region_name=region,
                config=Config(
                    retries={"max_attempts": 3, "mode": "standard"},
                    connect_timeout=5,
                    read_timeout=30,
                    # Self-hosted and most non-AWS providers want bucket names in the path.
                    s3={"addressing_style": "path"} if endpoint_url else {},
                ),
            )
        self.client = client

    def _call(self, operation: str, **kwargs):
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            return getattr(self.client, operation)(Bucket=self.bucket, **kwargs)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in ("NoSuchKey", "404", "NotFound"):
                return None
            log.warning("storage %s failed: %s", operation, code or error)
            raise StorageUnavailable(f"storage {operation} failed: {code}") from error
        except BotoCoreError as error:
            log.warning("storage %s failed: %s", operation, type(error).__name__)
            raise StorageUnavailable(f"storage {operation} failed") from error

    def save(self, key: str, data: bytes) -> None:
        extra = {"ServerSideEncryption": self.encryption} if self.encryption else {}
        self._call("put_object", Key=check_key(key), Body=data, **extra)

    def read(self, key: str) -> bytes | None:
        from botocore.exceptions import BotoCoreError

        response = self._call("get_object", Key=check_key(key))
        if response is None:
            return None
        try:
            return response["Body"].read()
        except BotoCoreError as error:
            raise StorageUnavailable("storage read failed") from error

    def read_chunks(self, key: str, chunk_size: int = CHUNK) -> Iterator[bytes] | None:
        """The object in blocks, or None if it does not exist. A failure part-way raises StorageUnavailable."""
        response = self._call("get_object", Key=check_key(key))
        if response is None:
            return None
        body = response["Body"]

        def blocks() -> Iterator[bytes]:
            try:
                yield from body.iter_chunks(chunk_size)
            except StorageUnavailable:
                raise
            except Exception as error:  # botocore, urllib3 or socket errors mid-stream
                raise StorageUnavailable(f"storage read failed: {type(error).__name__}") from error
            finally:
                body.close()

        return blocks()

    def delete(self, key: str) -> None:
        self._call("delete_object", Key=check_key(key))

    def list_objects(self, prefix: str) -> list[StoredObject]:
        """Every object whose key starts with prefix, page by page. A failure part-way raises
        StorageUnavailable: never a partial list."""
        found: list[StoredObject] = []
        token = None
        while True:
            page = self._call("list_objects_v2", Prefix=prefix, **({"ContinuationToken": token} if token else {}))
            if page is None:
                raise StorageUnavailable("storage listing failed: bucket not found")
            for entry in page.get("Contents", []):
                modified = entry["LastModified"]
                if modified.tzinfo is None:
                    modified = modified.replace(tzinfo=timezone.utc)
                found.append(StoredObject(entry["Key"], modified))
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
            if not token:
                raise StorageUnavailable("storage listing failed: truncated page without a continuation token")
        return sorted(found, key=lambda o: o.key)

    def list_keys(self, prefix: str) -> list[str]:
        return [o.key for o in self.list_objects(prefix)]


def build_storage(config: Settings) -> Storage:
    if config.storage == "s3":
        return S3Storage(config.s3_bucket, endpoint_url=config.s3_endpoint_url, region=config.s3_region,
                         encryption=config.s3_encryption)
    return LocalStorage(config.media_dir)


storage: Storage = build_storage(settings)
