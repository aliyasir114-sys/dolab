"""Deletion ledger entries v1 (plan 4.10.1ج): the server's part of SIG1-SIG10, on the shared
test vectors in docs/specs/ledger-entry-v1-vectors.json. The reconciliation job and the restore
script will run the same vectors when they are built."""
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import ledger

ROOT = Path(__file__).resolve().parents[3]
VECTORS = json.loads((ROOT / "docs/specs/ledger-entry-v1-vectors.json").read_text())
SPEC = (ROOT / "docs/specs/ledger-entry-v1.md").read_text()
API_DIR = Path(__file__).resolve().parents[1]
KEYS = {k["key_id"]: ledger.private_key_from_seed(k["seed_b64url"]) for k in VECTORS["keys"]}
PUBLIC = ledger.public_keys(",".join(f"{k['key_id']}:{k['public_b64url']}" for k in VECTORS["keys"]))


def test_the_pinned_cryptography_is_the_one_tested():
    pinned = re.search(r"^cryptography==(\S+)$", (API_DIR / "constraints.txt").read_text(), re.M).group(1)
    assert version("cryptography") == pinned
    major = int(pinned.split(".")[0])
    assert 44 <= major < 51  # the range in requirements.txt


def test_rfc_8032_test_1():
    case = VECTORS["rfc8032_test_1"]
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(case["seed_hex"]))
    assert ledger.public_key_text(key) == ledger._b64url(bytes.fromhex(case["public_hex"]))
    assert key.sign(bytes.fromhex(case["message_hex"])).hex() == case["signature_hex"]


def test_the_spec_template_is_the_one_the_code_builds():
    block = re.search(r"```\n(DOLAB-LEDGER-ENTRY\n.*?)```", SPEC, re.S).group(1)
    entry = ledger.parse_entry(VECTORS["valid"][0]["json"].encode())
    expected = ledger.signed_bytes(entry).decode()
    template = re.sub(r"=(.*)", lambda m: "=<" + m.group(1) + ">", expected)  # values as placeholders
    names = [line.split("=")[0] for line in block.strip().splitlines()]
    assert names == [line.split("=")[0] for line in template.strip().splitlines()]


# --------------------------------------------------------------- SIG1: same bytes, same signature

def test_sig1_keys_derive_their_published_public_keys():
    for item in VECTORS["keys"]:
        assert ledger.public_key_text(KEYS[item["key_id"]]) == item["public_b64url"]
    assert ledger.TEST_PUBLIC_KEYS == {k["key_id"]: k["public_b64url"] for k in VECTORS["keys"]}


@pytest.mark.parametrize("case", VECTORS["valid"], ids=lambda c: c["name"])
def test_sig1_every_valid_vector_is_built_signed_and_accepted_exactly(case):
    entry = ledger.verify_entry(case["json"].encode(), case["object_name"], PUBLIC)
    assert ledger.signed_bytes(entry).hex() == case["signed_bytes_hex"]
    assert entry.sig == case["signature_b64url"]
    # Signing the same fields again gives the same signature, byte for byte (Ed25519 is deterministic).
    again = ledger.make_entry(KEYS[entry.key_id], entry.key_id, entry.entity_type, entry.entity_id,
                              entry.user_id, entry.deleted_at, entry_id=entry.entry_id)
    assert again.sig == case["signature_b64url"]
    assert again.object_name == case["object_name"]


def test_sig1_the_stored_object_is_compact_json_in_spec_order():
    for case in VECTORS["valid"][:3]:
        entry = ledger.parse_entry(case["json"].encode())
        assert entry.to_json().decode() == case["json"]
        assert list(json.loads(entry.to_json())) == list(ledger.FIELDS)


# --------------------------------------------------------------- SIG2-SIG9: refused, never corrected

@pytest.mark.parametrize("case", VECTORS["invalid"], ids=lambda c: f"{c['test']}-{c['name']}")
def test_every_invalid_vector_is_refused_with_its_reason(case):
    with pytest.raises(ledger.LedgerError) as refused:
        ledger.verify_entry(case["json"].encode(), case["object_name"], PUBLIC)
    assert refused.value.code == case["error"]


def test_the_invalid_vectors_cover_sig2_to_sig9():
    assert {c["test"] for c in VECTORS["invalid"]} == {f"SIG{n}" for n in range(2, 10)} - {"SIG3"}
    changed = {c["name"] for c in VECTORS["invalid"] if c["test"] == "SIG2"}
    # One character changed in every field, including v and the signature (key_id is SIG7).
    assert changed == {f"changed-{f}" for f in ledger.FIELDS if f != "key_id"}


def test_sig2_one_character_in_any_field_breaks_the_signature_built_here():
    good = json.loads(VECTORS["valid"][0]["json"])
    entry = ledger.parse_entry(VECTORS["valid"][0]["json"].encode())
    for field in ("entry_id", "entity_id", "user_id"):
        for position in range(0, 36, 7):
            value = good[field]
            if value[position] == "-":
                continue
            changed = dict(good, **{field: value[:position] + ("a" if value[position] != "a" else "b") + value[position + 1:]})
            name = entry.object_name if field != "entry_id" else entry.object_name.replace(good["entry_id"], changed["entry_id"])
            with pytest.raises(ledger.LedgerError) as refused:
                ledger.verify_entry(json.dumps(changed).encode(), name, PUBLIC)
            assert refused.value.code == "bad_signature", (field, position)


def test_sig9_the_domain_line_is_part_of_what_is_signed():
    entry = ledger.parse_entry(VECTORS["valid"][0]["json"].encode())
    assert ledger.signed_bytes(entry).startswith(b"DOLAB-LEDGER-ENTRY\nv=1\n")
    assert ledger.signed_bytes(entry).endswith(b"Z\n") and b"\r" not in ledger.signed_bytes(entry)


# --------------------------------------------------------------- SIG10 and keys

def test_sig10_the_published_test_keys_cannot_sign_in_staging_or_production():
    for environment in ("staging", "production"):
        for key in KEYS.values():
            with pytest.raises(ledger.LedgerError) as refused:
                ledger.check_signing_key_allowed(environment, key)
            assert refused.value.code == "test_key"
        ledger.check_signing_key_allowed(environment, Ed25519PrivateKey.generate())  # a real key is fine
    for environment in ("development", "test"):
        for key in KEYS.values():
            ledger.check_signing_key_allowed(environment, key)


@pytest.mark.parametrize("spec", [
    "test-1:short", "Test-1:" + "A" * 43, "test-1:" + "A" * 43 + ",test-1:" + "B" * 43, "no-colon",
    "test-1:" + "A" * 42 + "=",
])
def test_public_key_lists_are_checked(spec):
    with pytest.raises(ledger.LedgerError):
        ledger.public_keys(spec)


@pytest.mark.parametrize("seed", ["", "A" * 42, "A" * 44, "A" * 42 + "=", "A" * 42 + "B"])
def test_signing_keys_are_checked(seed):
    with pytest.raises(ledger.LedgerError):
        ledger.private_key_from_seed(seed)


def test_make_entry_formats_time_and_refuses_bad_values():
    key = KEYS["test-1"]
    moment = datetime(2026, 11, 25, 11, 14, 3, 120458, tzinfo=timezone(timedelta(hours=2)))
    entry = ledger.make_entry(key, "test-1", "photo", str(uuid.uuid4()), str(uuid.uuid4()), moment)
    assert entry.deleted_at == "2026-11-25T09:14:03.120458Z"
    assert entry.object_name == f"2026/11/25/{entry.entry_id}.json"
    assert ledger.verify_entry(entry.to_json(), entry.object_name, PUBLIC) == entry
    with pytest.raises(ValueError):
        ledger.make_entry(key, "test-1", "photo", str(uuid.uuid4()), str(uuid.uuid4()), datetime(2026, 11, 25))
    with pytest.raises(ledger.LedgerError):
        ledger.make_entry(key, "test-1", "profile", str(uuid.uuid4()), str(uuid.uuid4()), moment)
    with pytest.raises(ledger.LedgerError):
        ledger.make_entry(key, "test-1", "item", str(uuid.uuid4()).upper(), str(uuid.uuid4()), moment)
