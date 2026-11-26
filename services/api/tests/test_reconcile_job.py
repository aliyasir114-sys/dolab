"""The reconciliation job without a database (notice #57): configuration refusals, the read-only
reader, verification of every entry, and the order of a run (process, purge, record)."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import ledger
from app.jobs import reconcile as R

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
SIGNER = ledger.private_key_from_seed(ledger.TEST_SEEDS["test-1"])
KEYS = ledger.public_keys(",".join(f"{k}:{v}" for k, v in ledger.TEST_PUBLIC_KEYS.items()))
DB_URL = "postgresql+psycopg://dolab_reconciler@db.example/dolab"


def entry(age=timedelta(days=1), entity_type="item", key_id="test-1"):
    return ledger.make_entry(SIGNER, key_id, entity_type, str(uuid.uuid4()), str(uuid.uuid4()), NOW - age,
                             environment="test")


class FakeReader:
    """Records every call; has no way to write."""

    def __init__(self, objects: dict[str, bytes], fail_listing=False, fail_get=()):
        self.objects, self.fail_listing, self.fail_get, self.calls = objects, fail_listing, set(fail_get), []

    def list_names(self, prefix):
        self.calls.append(("list", prefix))
        if self.fail_listing:
            raise R.ListingFailed("bucket unreachable")
        return sorted(name for name in self.objects if name.startswith(prefix))

    def get(self, name):
        self.calls.append(("get", name))
        if name in self.fail_get:
            raise ConnectionError("reset")
        return self.objects[name]


class FakeDb:
    def __init__(self, outcome="deleted", fail_for=()):
        self.outcome, self.fail_for = outcome, set(fail_for)
        self.calls, self.recorded = [], []

    def lock(self):
        return True

    def reconcile(self, e, dry_run):
        self.calls.append(("reconcile", e.entry_id, dry_run))
        if e.entry_id in self.fail_for:
            raise RuntimeError("statement timeout")
        return "would_delete" if dry_run else self.outcome

    def purge(self, dry_run):
        self.calls.append(("purge", dry_run))
        return 2

    def record(self, report):
        self.calls.append(("record",))
        self.recorded.append(report)


def ledger_of(*entries):
    return {e.object_name: e.to_json() for e in entries}


def go(objects, db=None, **kwargs):
    db = db or FakeDb()
    reader = objects if isinstance(objects, FakeReader) else FakeReader(objects)
    return R.run(reader, db, KEYS, now=lambda: NOW, **kwargs), db, reader


# ------------------------------------------------------------------ configuration

def env(**values):
    base = {"DOLAB_ENV": "test", "DOLAB_RECONCILER_DATABASE_URL": DB_URL}
    base.update(values)
    return {k: v for k, v in base.items() if v is not None}


def test_the_signing_key_must_not_reach_the_job():
    with pytest.raises(R.Refused, match="SIGNING_KEY"):
        R.load_config(env(DOLAB_LEDGER_SIGNING_KEY=ledger.TEST_SEEDS["test-1"]))


@pytest.mark.parametrize("values, message", [
    ({"DOLAB_ENV": "prod"}, "DOLAB_ENV"),
    ({"DOLAB_RECONCILER_DATABASE_URL": None}, "DATABASE_URL"),
    ({"DOLAB_RECONCILER_DATABASE_URL": "sqlite:///x.db"}, "DATABASE_URL"),
    ({"DOLAB_ENV": "production", "DOLAB_LEDGER_BUCKET": "b"}, "PUBLIC_KEYS must be set"),
    ({"DOLAB_ENV": "staging", "DOLAB_LEDGER_BUCKET": "b",
      "DOLAB_LEDGER_PUBLIC_KEYS": f"test-1:{ledger.TEST_PUBLIC_KEYS['test-1']}"}, "test key"),
    ({"DOLAB_LEDGER_PUBLIC_KEYS": "test-1:not-a-key"}, "refused: bad_key"),
    ({"DOLAB_ENV": "production", "DOLAB_LEDGER_STORAGE": "local",
      "DOLAB_LEDGER_PUBLIC_KEYS": "prod-1:" + "A" * 43}, "must be 's3'"),
    ({"DOLAB_LEDGER_STORAGE": "s3"}, "LEDGER_BUCKET"),
    ({"DOLAB_ENV": "production", "DOLAB_LEDGER_BUCKET": "b", "DOLAB_S3_ENDPOINT_URL": "http://minio:9000",
      "DOLAB_LEDGER_PUBLIC_KEYS": "prod-1:" + "A" * 43}, "https"),
])
def test_configurations_it_refuses(values, message):
    with pytest.raises(R.Refused, match=message):
        R.load_config(env(**values))


def test_development_trusts_the_published_test_keys_only_by_default():
    config = R.load_config(env())
    assert set(config.public_keys) == set(ledger.TEST_PUBLIC_KEYS) and config.storage == "local"
    production = R.load_config(env(DOLAB_ENV="production", DOLAB_LEDGER_BUCKET="ledger",
                                   DOLAB_LEDGER_PUBLIC_KEYS="prod-1:" + "A" * 43))
    assert set(production.public_keys) == {"prod-1"} and production.storage == "s3"


# ------------------------------------------------------------------ the reader cannot write

class RecordingS3:
    """Stands in for boto3: anything but listing and reading fails the test."""

    def __init__(self, objects):
        self.objects, self.calls = objects, []

    def list_objects_v2(self, **kwargs):
        self.calls.append("list_objects_v2")
        keys = sorted(k for k in self.objects if k.startswith(kwargs["Prefix"]))
        start = int(kwargs.get("ContinuationToken", 0))
        page = keys[start:start + 2]
        more = start + 2 < len(keys)
        return {"Contents": [{"Key": k} for k in page], "IsTruncated": more,
                **({"NextContinuationToken": str(start + 2)} if more else {})}

    def get_object(self, **kwargs):
        self.calls.append("get_object")
        assert kwargs["Range"] == f"bytes=0-{ledger.MAX_ENTRY_BYTES}"
        data = self.objects[kwargs["Key"]][: ledger.MAX_ENTRY_BYTES + 1]
        return {"Body": type("Body", (), {"read": lambda self, n: data[:n]})()}

    def __getattr__(self, name):
        raise AssertionError(f"the reader called {name}")


def test_the_s3_reader_only_lists_and_reads():
    entries = [entry() for _ in range(5)]
    client = RecordingS3(ledger_of(*entries))
    reader = R.S3LedgerReader("ledger", client)
    report = R.run(reader, FakeDb(), KEYS, now=lambda: NOW)
    assert report.valid == 5 and report.read == 5
    assert set(client.calls) == {"list_objects_v2", "get_object"}
    assert [m for m in dir(R.S3LedgerReader) if not m.startswith("_")] == ["get", "list_names"]
    assert [m for m in dir(R.LocalLedgerReader) if not m.startswith("_")] == ["get", "list_names"]


def test_the_local_reader_reads_the_ledger_folder(tmp_path):
    good = entry()
    path = tmp_path / good.object_name
    path.parent.mkdir(parents=True)
    path.write_bytes(good.to_json())
    big = tmp_path / good.object_name.replace(good.entry_id, str(uuid.uuid4()))
    big.write_bytes(b"x" * 5000)
    report = R.run(R.LocalLedgerReader(str(tmp_path)), FakeDb(), KEYS, now=lambda: NOW)
    assert (report.read, report.valid, report.invalid) == (2, 1, 1)
    before = sorted(p.name for p in tmp_path.rglob("*"))
    assert sorted(p.name for p in tmp_path.rglob("*")) == before


def test_the_window_covers_31_days_by_day():
    prefixes = R.day_prefixes(NOW)
    assert prefixes[0] == "2026/10/26/" and prefixes[-1] == "2026/11/26/" and len(prefixes) == 32


# ------------------------------------------------------------------ verification

def test_valid_entries_are_reconciled_in_full():
    entries = [entry(entity_type=t) for t in ("item", "photo", "user")]
    report, db, _ = go(ledger_of(*entries))
    assert (report.read, report.valid, report.invalid, report.errors) == (3, 3, 0, 0)
    assert report.outcomes["deleted"] == 3 and report.purged == 2
    assert [c[0] for c in db.calls] == ["reconcile"] * 3 + ["purge", "record"]
    assert R.exit_code(report) == R.EXIT_OK


def test_invalid_entries_are_never_acted_on_and_stop_the_purge():
    good = entry()
    other_key = ledger.make_entry(ledger.private_key_from_seed(ledger.TEST_SEEDS["test-1"]), "test-2", "item",
                                  str(uuid.uuid4()), str(uuid.uuid4()), NOW - timedelta(hours=1),
                                  environment="test")  # signed by test-1, claims test-2
    unknown = entry(key_id="nobody")
    tampered = entry()
    tampered_raw = tampered.to_json().replace(b'"item"', b'"user"')
    misnamed = entry()
    objects = {
        good.object_name: good.to_json(),
        other_key.object_name: other_key.to_json(),
        unknown.object_name: unknown.to_json(),
        tampered.object_name: tampered_raw,
        misnamed.object_name.replace(misnamed.entry_id, str(uuid.uuid4())): misnamed.to_json(),
        "2026/11/25/not-json.json": b"{not json",
    }
    report, db, _ = go(objects)
    assert (report.read, report.valid, report.invalid) == (6, 1, 5)
    assert [c for c in db.calls if c[0] == "reconcile"] == [("reconcile", good.entry_id, False)]
    assert report.purged is None and ("purge", False) not in db.calls
    assert db.recorded and R.exit_code(report) == R.EXIT_PROBLEMS


def test_entries_outside_the_window():
    old = entry(age=timedelta(days=31, minutes=1))
    edge = entry(age=timedelta(days=30, hours=23))
    future = ledger.make_entry(SIGNER, "test-1", "item", str(uuid.uuid4()), str(uuid.uuid4()),
                               NOW + timedelta(hours=1), environment="test")
    report, db, _ = go(ledger_of(old, edge, future))
    assert (report.too_old, report.valid, report.invalid) == (1, 1, 1)
    assert [c[1] for c in db.calls if c[0] == "reconcile"] == [edge.entry_id]


# ------------------------------------------------------------------ the order of a run

def test_a_failed_listing_processes_nothing_and_is_recorded_as_failed():
    report, db, reader = go(FakeReader(ledger_of(entry()), fail_listing=True))
    assert report.listing_failed and report.errors == 1
    assert db.calls == [("record",)] and not any(c[0] == "get" for c in reader.calls)
    assert R.exit_code(report) == R.EXIT_LISTING_FAILED


def test_the_listing_is_complete_before_any_entry_is_read():
    _, _, reader = go(ledger_of(entry(), entry(age=timedelta(days=20))))
    kinds = [c[0] for c in reader.calls]
    assert kinds == ["list"] * 32 + ["get", "get"]


def test_one_failing_entry_does_not_stop_the_others_but_stops_the_purge():
    entries = [entry() for _ in range(4)]
    objects = ledger_of(*entries)
    report, db, _ = go(FakeReader(objects, fail_get={entries[0].object_name}),
                       db=FakeDb(fail_for={entries[1].entry_id}))
    assert report.errors == 2 and report.outcomes["deleted"] == 2
    assert report.purged is None and db.recorded and R.exit_code(report) == R.EXIT_PROBLEMS


def test_an_owner_mismatch_fails_the_run_and_stops_the_purge():
    report, db, _ = go(ledger_of(entry(), entry()), db=FakeDb(outcome="owner_mismatch"))
    assert report.outcomes["owner_mismatch"] == 2 and report.errors == 0 and report.invalid == 0
    assert not report.clean and report.purged is None and ("purge", False) not in db.calls
    assert db.recorded and R.exit_code(report) == R.EXIT_PROBLEMS


def test_unknown_outcomes_are_errors():
    report, _, _ = go(ledger_of(entry()), db=FakeDb(outcome="surprise"))
    assert report.errors == 1 and report.purged is None


def test_a_dry_run_records_nothing():
    report, db, _ = go(ledger_of(entry(), entry()), dry_run=True)
    assert report.outcomes["would_delete"] == 2 and report.purged == 2
    assert db.calls[-1] == ("purge", True) and not db.recorded
    assert all(c[2] is True for c in db.calls if c[0] == "reconcile")


def test_the_summary_has_no_user_or_entity_ids(caplog):
    e = entry()
    caplog.set_level("INFO")
    go(ledger_of(e))
    assert e.user_id not in caplog.text and e.entity_id not in caplog.text
    assert e.entry_id in caplog.text
