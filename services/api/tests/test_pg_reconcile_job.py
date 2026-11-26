"""The reconciliation job end to end on PostgreSQL (notice #57): connected as the real
dolab_reconciler, reading a ledger folder of signed entries, against the real functions."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_pg_roles import ADMIN_URL, E, _conninfo, connect, rows, value

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="needs PostgreSQL (DOLAB_TEST_PG_ADMIN_URL)")

from app import deletion, ledger, ledger_store  # noqa: E402
from app.jobs import reconcile as R  # noqa: E402
from tests.test_pg_deleter_functions import audit, db_client, intent, state, wardrobe  # noqa: E402,F401

SIGNER = ledger.private_key_from_seed(ledger.TEST_SEEDS["test-1"])
KEYS = ledger.public_keys(",".join(f"{k}:{v}" for k, v in ledger.TEST_PUBLIC_KEYS.items()))


def signed(entity_type, entity_id, user_id, age=timedelta(hours=1)):
    return ledger.make_entry(SIGNER, "test-1", entity_type, entity_id, user_id,
                             datetime.now(timezone.utc) - age, environment="test")


def put(folder, entry, raw=None):
    path = folder / entry.object_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw if raw is not None else entry.to_json())


def job(folder, dry_run=False):
    db = R.PgDatabase(_conninfo(E.RECONCILER))
    try:
        assert db.lock()
        return R.run(R.LocalLedgerReader(str(folder)), db, KEYS, dry_run=dry_run)
    finally:
        db.close()


def runs():
    with connect() as admin:
        return rows(admin, "SELECT outcome, counts FROM dolab.audit_log WHERE action = 'reconcile.run' ORDER BY at")


def test_it_completes_a_half_done_deletion_then_finds_it_done(db_client, signup, monkeypatch, tmp_path):
    headers, user_id, item_id, _ = wardrobe(db_client, signup)
    monkeypatch.setattr(ledger_store, "writer", ledger_store.LocalLedger(str(tmp_path)))
    real = deletion.apply_deletion

    def apply_then_fail(db, *args):
        real(db, *args)
        raise RuntimeError("connection lost before commit")

    monkeypatch.setattr(deletion, "apply_deletion", apply_then_fail)
    with pytest.raises(RuntimeError):
        db_client.delete(f"/items/{item_id}", headers=headers)
    monkeypatch.setattr(deletion, "apply_deletion", real)
    assert db_client.get(f"/items/{item_id}", headers=headers).status_code == 200  # still there

    report = job(tmp_path)
    assert (report.read, report.valid, report.outcomes["deleted"], report.purged) == (1, 1, 1, 0)
    assert R.exit_code(report) == R.EXIT_OK
    assert db_client.get(f"/items/{item_id}", headers=headers).status_code == 404
    assert state()["deletion_intents"] == []

    again = job(tmp_path)
    assert again.outcomes["already_done"] == 1 and again.outcomes["deleted"] == 0
    assert [outcome for outcome, _ in runs()] == ["recorded", "recorded"]


def test_every_outcome_through_the_job(db_client, signup, tmp_path):
    headers, mine, item_id, _ = wardrobe(db_client, signup)
    their_headers, _, their_item, _ = wardrobe(db_client, signup, "other@example.com")
    # absent: the server finished the deletion but the intent was left behind.
    gone = new_gone_item(db_client, headers)
    absent = signed("item", gone, mine)
    intent_for(absent)
    # no intent: an entry the server finished cleanly.
    finished = signed("item", str(uuid.uuid4()), mine)
    # owner mismatch: an entry and intent naming someone else's item.
    forged = signed("item", their_item, mine)
    intent_for(forged)
    for e in (absent, finished, forged):
        put(tmp_path, e)
    stale = intent("item", item_id, mine, age=timedelta(days=32))
    before_theirs = db_client.get("/items", headers=their_headers).json()

    report = job(tmp_path)
    assert {k: v for k, v in report.outcomes.items() if v} == {"absent": 1, "no_intent": 1, "owner_mismatch": 1}
    # An owner mismatch fails the run and stops the purge (review of #58).
    assert R.exit_code(report) == R.EXIT_PROBLEMS and report.purged is None
    assert stale in [i[0] for i in state()["deletion_intents"]]
    assert db_client.get("/items", headers=their_headers).json() == before_theirs
    assert ("reconciler", "reconcile.entry", forged.entry_id, "item", "owner_mismatch") in audit()
    ((outcome, counts),) = runs()
    assert outcome == "failed" and counts["absent"] == 1 and counts["owner_mismatch"] == 1
    assert db_client.get(f"/items/{item_id}", headers=headers).status_code == 200


def test_a_dry_run_changes_nothing(db_client, signup, tmp_path):
    _, user_id, item_id, _ = wardrobe(db_client, signup)
    e = signed("item", item_id, user_id)
    intent_for(e)
    intent("item", item_id, user_id, age=timedelta(days=40))
    put(tmp_path, e)
    before = state()
    report = job(tmp_path, dry_run=True)
    assert report.outcomes["would_delete"] == 1 and report.purged == 1
    assert state() == before


def test_an_invalid_entry_stops_the_purge_and_fails_the_run(db_client, signup, tmp_path):
    _, user_id, item_id, _ = wardrobe(db_client, signup)
    stale = intent("item", item_id, user_id, age=timedelta(days=32))
    good = signed("item", item_id, user_id)
    intent_for(good)
    bad = signed("item", str(uuid.uuid4()), user_id)
    put(tmp_path, good)
    put(tmp_path, bad, bad.to_json().replace(b'"item"', b'"user"'))  # signature no longer matches

    report = job(tmp_path)
    assert (report.valid, report.invalid, report.outcomes["deleted"], report.purged) == (1, 1, 1, None)
    assert R.exit_code(report) == R.EXIT_PROBLEMS
    assert [i[0] for i in state()["deletion_intents"]] == [stale]  # not purged
    ((outcome, counts),) = runs()
    assert outcome == "failed" and counts["bad_signature"] == 1

    (tmp_path / bad.object_name).unlink()
    clean = job(tmp_path)
    assert clean.purged == 1 and state()["deletion_intents"] == []
    assert [o for o, _ in runs()] == ["failed", "recorded"]


def test_the_purge_removes_only_intents_older_than_31_days(db_client, signup):
    _, user_id, item_id, _ = wardrobe(db_client, signup)
    old = intent("item", item_id, user_id, age=timedelta(days=31, minutes=1))
    kept = intent("item", item_id, user_id, age=timedelta(days=30, hours=23))
    with connect(E.RECONCILER) as conn:
        assert value(conn, "SELECT dolab.reconcile_purge_intents(true)") == 1
        assert {i[0] for i in state()["deletion_intents"]} == {old, kept}
        assert value(conn, "SELECT dolab.reconcile_purge_intents(false)") == 1
    assert [i[0] for i in state()["deletion_intents"]] == [kept]
    with connect() as admin:
        assert rows(admin, "SELECT action, outcome, counts FROM dolab.audit_log") == [
            ("reconcile.purge_intents", "purged", {"purged": 1})]


def test_a_second_run_waits_for_the_first(db_client, tmp_path, monkeypatch):
    for name, value_ in {"DOLAB_ENV": "test", "DOLAB_RECONCILER_DATABASE_URL": _conninfo(E.RECONCILER),
                         "DOLAB_LEDGER_STORAGE": "local", "DOLAB_LEDGER_DIR": str(tmp_path)}.items():
        monkeypatch.setenv(name, value_)
    monkeypatch.delenv("DOLAB_LEDGER_SIGNING_KEY", raising=False)
    monkeypatch.delenv("DOLAB_LEDGER_PUBLIC_KEYS", raising=False)
    with connect(E.RECONCILER) as holder:
        assert value(holder, "SELECT pg_try_advisory_lock(%s)", R.LOCK_KEY) is True
        assert R.main([]) == R.EXIT_LOCKED
        assert runs() == []
    assert R.main([]) == R.EXIT_OK
    assert [o for o, _ in runs()] == ["recorded"]


def test_it_will_only_connect_as_the_reconciler():
    for role in (E.APP, E.DEPLOY):
        with pytest.raises(R.Refused, match="dolab_reconciler"):
            R.PgDatabase(_conninfo(role))


# ------------------------------------------------------------------ helpers

def intent_for(entry):
    with connect() as admin:
        admin.execute("INSERT INTO dolab.deletion_intents (entry_id, entity_type, entity_id, user_id, created_at) "
                      "VALUES (%s, %s, %s, %s, now())",
                      (entry.entry_id, entry.entity_type, entry.entity_id, entry.user_id))


def new_gone_item(client, headers):
    from tests.test_photos import new_item

    item_id = new_item(client, headers)
    assert client.delete(f"/items/{item_id}", headers=headers).status_code == 204
    return item_id

