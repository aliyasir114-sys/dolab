"""Storage key rules and the storage error, without the API's settings: shared by app.storage
and by jobs that run with their own, narrower configuration (app.jobs.file_deletions)."""
import re

# Keys are made by the server ("<user id>/<photo id>_original.jpg"); anything else is refused.
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$")


class StorageUnavailable(RuntimeError):
    """The storage could not be reached or refused the request. Callers answer 503 or retry later."""


def check_key(key: str) -> str:
    if len(key) > 512 or not _KEY.match(key) or ".." in key.split("/"):
        raise ValueError("invalid storage key")
    return key
