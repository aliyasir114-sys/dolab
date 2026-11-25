"""Deletion ledger entries, format version 1: build, sign, read and verify (plan 4.10.1).

The specification is docs/specs/ledger-entry-v1.md and its test vectors are
docs/specs/ledger-entry-v1-vectors.json; the server, the reconciliation job and the restore
script must all produce and accept exactly those bytes and signatures.

This module is pure: no storage, no database, no deletion. Writing entries to the ledger
bucket, the deletion flow, the reconciliation job and the restore script come later.

Signed bytes (UTF-8, all ASCII; every line ends with one LF; no CR, no spaces):

    DOLAB-LEDGER-ENTRY
    v=1
    key_id=<key_id>
    entry_id=<uuid>
    entity_type=<entity_type>
    entity_id=<uuid>
    user_id=<uuid>
    deleted_at=<YYYY-MM-DDTHH:MM:SS.ffffffZ>

Signature: Ed25519 (RFC 8032, pure Ed25519, not Ed25519ph or Ed25519ctx), 64 bytes, stored as
base64url without padding. JSON is only the container; the bytes are always rebuilt from the
template, never taken from the JSON text.
"""
import base64
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

VERSION = 1
DOMAIN = "DOLAB-LEDGER-ENTRY"
MAX_ENTRY_BYTES = 2048
FIELDS = ("v", "key_id", "entry_id", "entity_type", "entity_id", "user_id", "deleted_at", "sig")
ENTITY_TYPES = ("item", "photo", "user")
SIGNING_ENVIRONMENTS = ("development", "test", "staging", "production")

_KEY_ID = re.compile(r"[a-z0-9-]{1,32}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z")
_SIG = re.compile(r"[A-Za-z0-9_-]{86}")  # 64 bytes in base64url without padding
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

# The published test seeds (docs/specs/ledger-entry-v1-vectors.json): development and tests
# sign with test-1 when no key is configured. Not secret, and refused in staging and production.
TEST_SEEDS = {"test-1": "0WzzM2YUoxkR4x4tNq7k6gssXFyhAztdg6HpvbNP3tU"}

# Public keys of the published test key pairs (docs/specs/ledger-entry-v1-vectors.json). Their
# private halves are public knowledge, so staging and production refuse to sign with them.
TEST_PUBLIC_KEYS = {
    "test-1": "atFLJdul-xx1LsB1FIhOTkduocR2pbba3X2OtjJb_x8",
    "test-2": "Fyxg62wGwFNdUnW_WLA7e8PpOYxlUHmNKkm6HZu1S7I",
}


class LedgerError(ValueError):
    """An entry was refused. `code` names the rule it broke; nothing is ever corrected."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


@dataclass(frozen=True)
class Entry:
    key_id: str
    entry_id: str
    entity_type: str
    entity_id: str
    user_id: str
    deleted_at: str
    sig: str = ""

    @property
    def object_name(self) -> str:
        """Its name in the ledger bucket: YYYY/MM/DD/<entry_id>.json, the date from deleted_at."""
        return f"{self.deleted_at[0:4]}/{self.deleted_at[5:7]}/{self.deleted_at[8:10]}/{self.entry_id}.json"

    def to_json(self) -> bytes:
        """The stored object: compact JSON with the fields in specification order."""
        values = {"v": VERSION, **{name: getattr(self, name) for name in FIELDS[1:]}}
        return json.dumps(values, separators=(",", ":")).encode("ascii")


# ------------------------------------------------------------------ rules and bytes

def _check_fields(key_id: str, entry_id: str, entity_type: str, entity_id: str, user_id: str,
                  deleted_at: str) -> None:
    """Each value against its rule, exactly as given: never normalised."""
    if not _KEY_ID.fullmatch(key_id):
        raise LedgerError("bad_value", "key_id")
    for name, value in (("entry_id", entry_id), ("entity_id", entity_id), ("user_id", user_id)):
        if not _UUID.fullmatch(value):
            raise LedgerError("bad_value", name)
    if entity_type not in ENTITY_TYPES:
        raise LedgerError("bad_value", "entity_type")
    if not _TIMESTAMP.fullmatch(deleted_at):
        raise LedgerError("bad_value", "deleted_at")
    try:
        datetime.strptime(deleted_at, _TIMESTAMP_FORMAT)
    except ValueError as error:  # 2026-13-45 and the like
        raise LedgerError("bad_value", "deleted_at") from error


def signed_bytes(entry: Entry) -> bytes:
    """The exact bytes that are signed and verified, built from the template."""
    _check_fields(entry.key_id, entry.entry_id, entry.entity_type, entry.entity_id, entry.user_id, entry.deleted_at)
    lines = (
        DOMAIN,
        f"v={VERSION}",
        f"key_id={entry.key_id}",
        f"entry_id={entry.entry_id}",
        f"entity_type={entry.entity_type}",
        f"entity_id={entry.entity_id}",
        f"user_id={entry.user_id}",
        f"deleted_at={entry.deleted_at}",
    )
    return "".join(line + "\n" for line in lines).encode("ascii")


def format_time(moment: datetime) -> str:
    """deleted_at for an aware datetime: UTC, six fractional digits, Z."""
    if moment.tzinfo is None:
        raise ValueError("deleted_at must be timezone-aware")
    return moment.astimezone(timezone.utc).strftime(_TIMESTAMP_FORMAT)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _from_b64url(text: str, length: int, what: str) -> bytes:
    data = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    # Only the one canonical spelling: no padding, no stray bits in the last character.
    if len(data) != length or _b64url(data) != text:
        raise LedgerError("bad_value", what)
    return data


# ------------------------------------------------------------------ keys

def private_key_from_seed(seed_b64url: str):
    """DOLAB_LEDGER_SIGNING_KEY: the 32-byte Ed25519 seed in base64url without padding."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", seed_b64url or ""):
        raise LedgerError("bad_key", "signing key must be 32 bytes in base64url without padding")
    return Ed25519PrivateKey.from_private_bytes(_from_b64url(seed_b64url, 32, "signing key"))


def public_key_text(private_key) -> str:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    return _b64url(private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def public_keys(spec: str) -> dict:
    """The verification keys: "key_id:base64url,key_id:base64url"."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    keys = {}
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        key_id, _, text = part.partition(":")
        if not _KEY_ID.fullmatch(key_id) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", text) or key_id in keys:
            raise LedgerError("bad_key", f"public key entry {key_id!r}")
        keys[key_id] = Ed25519PublicKey.from_public_bytes(_from_b64url(text, 32, "public key"))
    return keys


def check_signing_key_allowed(environment: str | None, private_key) -> None:
    """SIG10: require a known environment and limit published test keys to development/tests."""
    if environment not in SIGNING_ENVIRONMENTS:
        raise LedgerError("bad_environment", "signing environment is missing or unknown")
    if environment in ("staging", "production") and public_key_text(private_key) in TEST_PUBLIC_KEYS.values():
        raise LedgerError("test_key", f"the published test key cannot be used in {environment}")


# ------------------------------------------------------------------ sign

def make_entry(private_key, key_id: str, entity_type: str, entity_id: str, user_id: str,
               deleted_at: datetime | str, entry_id: str | None = None,
               *, environment: str | None = None) -> Entry:
    """A signed entry for one deletion. Values must already satisfy the rules."""
    check_signing_key_allowed(environment, private_key)
    moment = deleted_at if isinstance(deleted_at, str) else format_time(deleted_at)
    unsigned = Entry(key_id=key_id, entry_id=entry_id or str(uuid.uuid4()), entity_type=entity_type,
                     entity_id=entity_id, user_id=user_id, deleted_at=moment)
    signature = private_key.sign(signed_bytes(unsigned))
    return Entry(**{**unsigned.__dict__, "sig": _b64url(signature)})


# ------------------------------------------------------------------ read and verify

def _no_duplicates(pairs: list) -> dict:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise LedgerError("bad_keys", "duplicate key")
    return dict(pairs)


def _no_constants(name: str):
    raise LedgerError("not_json", f"{name} is not allowed")


def parse_entry(raw: bytes) -> Entry:
    """Steps 1 and 2 of the verification: strict JSON, every rule, no normalisation."""
    if len(raw) > MAX_ENTRY_BYTES:
        raise LedgerError("too_large", f"over {MAX_ENTRY_BYTES} bytes")
    try:
        values = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_no_constants)
    except LedgerError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LedgerError("not_json", str(error)) from error
    if not isinstance(values, dict):
        raise LedgerError("not_json", "not an object")
    if set(values) != set(FIELDS):
        missing, extra = set(FIELDS) - set(values), set(values) - set(FIELDS)
        raise LedgerError("bad_keys", f"missing {sorted(missing)}, extra {sorted(extra)}")
    version = values["v"]
    if type(version) is not int:  # not a string, a float, or a boolean
        raise LedgerError("bad_type", "v must be a whole number")
    for name in FIELDS[1:]:
        if type(values[name]) is not str:
            raise LedgerError("bad_type", f"{name} must be a string")
    if version != VERSION:
        raise LedgerError("unsupported_version", f"v={version}")
    entry = Entry(**{name: values[name] for name in FIELDS[1:]})
    _check_fields(entry.key_id, entry.entry_id, entry.entity_type, entry.entity_id, entry.user_id, entry.deleted_at)
    if not _SIG.fullmatch(entry.sig):
        raise LedgerError("bad_value", "sig")
    _from_b64url(entry.sig, 64, "sig")
    return entry


def verify_entry(raw: bytes, object_name: str, keys: Mapping[str, object]) -> Entry:
    """Verify one stored entry (plan 4.10.1ج steps 1 to 4). Raises LedgerError; any failure
    must be logged and alerted on by the caller, and must never lead to a deletion."""
    from cryptography.exceptions import InvalidSignature

    entry = parse_entry(raw)
    if object_name != entry.object_name:
        raise LedgerError("name_mismatch", f"{object_name} is not {entry.object_name}")
    key = keys.get(entry.key_id)
    if key is None:
        raise LedgerError("unknown_key", entry.key_id)
    try:
        key.verify(_from_b64url(entry.sig, 64, "sig"), signed_bytes(entry))
    except InvalidSignature as error:
        raise LedgerError("bad_signature", entry.entry_id) from error
    return entry
