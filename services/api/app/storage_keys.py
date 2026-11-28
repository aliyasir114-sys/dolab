"""Storage key rules and the storage error, without the API's settings: shared by app.storage
and by jobs that run with their own, narrower configuration (app.jobs.file_deletions)."""
import re

# Keys are made by the server ("<user id>/<photo id>_original.jpg"); anything else is refused.
# Anchored with \A and \Z, and used with fullmatch: "$" would also accept a newline at the end
# (review of #60, Medium 3).
_KEY = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*\Z")


class StorageUnavailable(RuntimeError):
    """The storage could not be reached or refused the request. Callers answer 503 or retry later."""


_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# The only keys the server makes for photos: "<user id>/<photo id>_original.<ext>" and
# "<user id>/<photo id>_thumb.jpg". The orphan job and the file deletion job touch nothing else.
PHOTO_KEY = re.compile(rf"\A{_UUID}/{_UUID}_(original\.(jpg|png|webp)|thumb\.jpg)\Z")


def is_photo_key(key: object) -> bool:
    """True only for a photo key exactly as the server makes it: no other path, no extra
    character, no newline or control character anywhere. The one check every photo-file
    deletion goes through (the immediate attempt, a failed upload, the queue job, the orphan
    job) before anything is sent to storage."""
    return isinstance(key, str) and PHOTO_KEY.fullmatch(key) is not None


def check_key(key: str) -> str:
    if not isinstance(key, str) or len(key) > 512 or _KEY.fullmatch(key) is None or ".." in key.split("/"):
        raise ValueError("invalid storage key")
    return key
