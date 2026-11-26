"""The deleter functions (plan 4.10.1د; notice #55): dolab.apply_deletion, dolab.reconcile_apply,
dolab.reconcile_record_run. DB1-DB12, the parity of the Python and PostgreSQL deletion logic,
and the reconciliation of a deletion that was left half done.

PostgreSQL only (the CI PostgreSQL job): connects as the real dolab_reconciler and dolab_app
roles, and as the database owner to set up intents and to read the whole database.
"""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from tests.test_pg_roles import ADMIN_URL, E, PRIVILEGES, connect, denied, rows, value

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="needs PostgreSQL (DOLAB_TEST_PG_ADMIN_URL)")

if ADMIN_URL:
    from psycopg import errors

from app import clock, deletion
from app.db import SessionLocal
from tests.test_photos import new_item, upload

APPLY = "dolab.apply_deletion(text,uuid,uuid)"
RECONCILE = "dolab.reconcile_apply(uuid,text,uuid,uuid,boolean)"
RECORD = ("dolab.reconcile_record_run(timestamp with time zone,timestamp with time zone,integer,integer,integer,"
          "integer,integer,integer,integer,integer,integer)")
STATE_TABLES = ("users", "profiles", "items", "item_photos", "wear_logs", "item_status_changes", "donations",
                "donation_items", "refresh_tokens", "password_reset_tokens", "deletion_intents",
                "reconciled_entries", "audit_log")


@pytest.fixture
def db_client(client):
    """The API test client, with the deleter-only tables emptied as the owner."""
    with connect() as admin:
        admin.execute("DELETE FROM dolab.reconciled_entries")
        admin.execute("DELETE FROM dolab.audit_log")
    return client


def state() -> dict[str, list]:
    """Every row of every user data table, as the owner sees it."""
    with connect() as admin:
        return {t: rows(admin, f"SELECT * FROM dolab.{t} ORDER BY 1") for t in STATE_TABLES}


def intent(entity_type: str, entity_id: str, user_id: str, age: timedelta = timedelta(0)) -> str:
    """An intent as the server records it (step 1), made directly as the owner."""
    entry_id = str(uuid.uuid4())
    with connect() as admin:
        admin.execute(
            "INSERT INTO dolab.deletion_intents (entry_id, entity_type, entity_id, user_id, created_at) "
            "VALUES (%s, %s, %s, %s, now() - %s)", (entry_id, entity_type, entity_id, user_id, age))
    return entry_id


def reconcile(entry_id, entity_type, entity_id, user_id, dry_run=False) -> str:
    with connect(E.RECONCILER) as conn:
        return value(conn, "SELECT dolab.reconcile_apply(%s::uuid, %s, %s::uuid, %s::uuid, %s)",
                     entry_id, entity_type, entity_id, user_id, dry_run)


def audit() -> list[tuple]:
    with connect() as admin:
        return rows(admin, "SELECT actor, action, entry_id, entity_type, outcome FROM dolab.audit_log ORDER BY at")


def wardrobe(client, signup, email="owner@example.com"):
    headers = signup(email)
    user_id = client.get("/me", headers=headers).json()["id"]
    item_id = new_item(client, headers)
    photo = upload(client, headers, item_id).json()
    upload(client, headers, item_id)
    other_item = new_item(client, headers)
    upload(client, headers, other_item)
    client.post(f"/items/{item_id}/wear", headers=headers)
    client.patch(f"/items/{item_id}", json={"status": "laundry"}, headers=headers)
    client.put("/me/profile", json={"height_cm": 180}, headers=headers)
    return headers, user_id, item_id, photo["id"]


# ------------------------------------------------------------------ DB1-DB4: what the roles may do

def test_db1_the_reconciler_has_no_right_on_any_table():
    with connect(E.RECONCILER) as conn:
        tables = [t for (t,) in rows(conn, "SELECT table_name FROM information_schema.tables WHERE table_schema = 'dolab'")]
    with connect() as admin:
        all_tables = [t for (t,) in rows(admin, "SELECT tablename FROM pg_tables WHERE schemaname = 'dolab'")]
        for table in all_tables:
            for privilege in PRIVILEGES:
                assert value(admin, "SELECT has_table_privilege(%s, %s, %s)", E.RECONCILER, f"dolab.{table}", privilege) is False
            assert value(admin, "SELECT has_any_column_privilege(%s, %s, 'SELECT')", E.RECONCILER, f"dolab.{table}") is False
    assert tables == []  # it cannot even see a table's name in information_schema
    with connect(E.RECONCILER) as conn:
        for table in all_tables:
            for statement in (f"SELECT 1 FROM dolab.{table} LIMIT 1", f"DELETE FROM dolab.{table}",
                              f"TRUNCATE dolab.{table}", f"INSERT INTO dolab.{table} DEFAULT VALUES"):
                assert denied(conn, statement), statement


def test_db2_the_reconciler_cannot_create_switch_or_alter():
    with connect(E.RECONCILER) as conn:
        assert denied(conn, "CREATE TABLE dolab.x (i int)")
        assert denied(conn, "CREATE FUNCTION dolab.x() RETURNS int LANGUAGE sql AS 'SELECT 1'")
        assert denied(conn, f"ALTER FUNCTION {RECONCILE} SECURITY INVOKER")
        for role in (E.DELETER, E.OWNER, E.APP):
            assert denied(conn, f"SET ROLE {role}")


def test_db3_the_functions_are_locked_down():
    with connect() as admin:
        for function in (APPLY, RECONCILE, RECORD):
            owner, definer, config = rows(admin, "SELECT pg_get_userbyid(proowner), prosecdef, proconfig "
                                                 "FROM pg_proc WHERE oid = %s::regprocedure", function)[0]
            assert owner == E.DELETER and definer is True, function
            assert config == ["search_path=pg_catalog, dolab"], function
            assert value(admin, "SELECT has_function_privilege('public', %s, 'EXECUTE')", function) is False
        assert value(admin, "SELECT rolcanlogin FROM pg_roles WHERE rolname = %s", E.DELETER) is False


def test_db4_who_may_run_what():
    expected = {E.RECONCILER: {RECONCILE, RECORD}, E.APP: {APPLY}, E.DEPLOY: set()}
    with connect() as admin:
        functions = [f for (f,) in rows(admin, "SELECT p.oid::regprocedure::text FROM pg_proc p "
                                               "JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'dolab'")]
        for role, allowed in expected.items():
            have = {f for f in functions if value(admin, "SELECT has_function_privilege(%s, %s, 'EXECUTE')", role, f)}
            assert have == allowed, role


def test_the_api_cannot_reconcile_or_read_the_audit_log():
    with connect(E.APP) as conn:
        assert denied(conn, "SELECT dolab.reconcile_apply(gen_random_uuid(), 'item', gen_random_uuid(), "
                            "gen_random_uuid(), true)")
        assert denied(conn, "SELECT 1 FROM dolab.audit_log")
        assert denied(conn, "SELECT 1 FROM dolab.reconciled_entries")


# ------------------------------------------------------------------ DB5-DB12: reconcile_apply

def test_db5_no_intent_changes_nothing(db_client, signup):
    _, user_id, item_id, _ = wardrobe(db_client, signup)
    before = state()
    assert reconcile(str(uuid.uuid4()), "item", item_id, user_id) == "no_intent"
    assert state() == before


def test_db6_a_mismatch_with_the_intent_or_the_owner_deletes_nothing(db_client, signup):
    _, mine, item_id, _ = wardrobe(db_client, signup)
    _, theirs, their_item, _ = wardrobe(db_client, signup, "other@example.com")
    entry = intent("item", item_id, mine)
    before = state()
    # The call names a different user than the intent: no intent.
    assert reconcile(entry, "item", item_id, theirs) == "no_intent"
    # Different entity, or type: no intent.
    assert reconcile(entry, "item", their_item, mine) == "no_intent"
    assert reconcile(entry, "photo", item_id, mine) == "no_intent"
    assert state() == before
    # The intent itself names an entity of another user: owner mismatch, audited, nothing deleted.
    forged = intent("item", their_item, mine)
    assert reconcile(forged, "item", their_item, mine) == "owner_mismatch"
    after = state()
    assert {k: v for k, v in after.items() if k not in ("audit_log", "deletion_intents")} == \
        {k: v for k, v in before.items() if k not in ("audit_log", "deletion_intents")}
    assert audit() == [("reconciler", "reconcile.entry", forged, "item", "owner_mismatch")]
    # An account is its own owner only.
    forged_user = intent("user", theirs, mine)
    assert reconcile(forged_user, "user", theirs, mine) == "owner_mismatch"


@pytest.mark.parametrize("what", ["item", "photo", "user"])
def test_db7_db8_a_matching_intent_is_completed_once(db_client, signup, what):
    _, user_id, item_id, photo_id = wardrobe(db_client, signup)
    _, other, other_item, _ = wardrobe(db_client, signup, "other@example.com")
    entity = {"item": item_id, "photo": photo_id, "user": user_id}[what]
    entry = intent(what, entity, user_id)
    others_before = {t: [r for r in rs if other in map(str, r)] for t, rs in state().items()}

    assert reconcile(entry, what, entity, user_id) == "deleted"
    after = state()
    for table, table_rows in after.items():
        if table in ("reconciled_entries", "audit_log"):
            continue
        assert not any(entity in map(str, r) for r in table_rows), (table, "entity left behind")
    assert [r[0] for r in after["reconciled_entries"]] == [entry]
    assert after["deletion_intents"] == []
    assert audit() == [("reconciler", "reconcile.entry", entry, what, "deleted")]
    # Nobody else's rows were touched.
    assert {t: [r for r in rs if other in map(str, r)] for t, rs in after.items()} == others_before
    # DB8: the same call again.
    before_again = state()
    assert reconcile(entry, what, entity, user_id) == "already_done"
    assert state() == before_again


def test_db9_a_dry_run_changes_nothing(db_client, signup):
    _, user_id, item_id, _ = wardrobe(db_client, signup)
    entry = intent("item", item_id, user_id)
    before = state()
    assert reconcile(entry, "item", item_id, user_id, dry_run=True) == "would_delete"
    assert state() == before


def test_db10_an_unknown_entity_type_is_an_error(db_client, signup):
    _, user_id, item_id, _ = wardrobe(db_client, signup)
    entry = intent("item", item_id, user_id)
    before = state()
    for kind in ("profile", "ITEM", ""):
        with pytest.raises(errors.InvalidParameterValue):
            reconcile(entry, kind, item_id, user_id)
    assert state() == before


def test_db11_an_intent_older_than_30_days_is_not_acted_on(db_client, signup):
    _, user_id, item_id, _ = wardrobe(db_client, signup)
    old = intent("item", item_id, user_id, age=timedelta(days=31))
    recent = intent("item", item_id, user_id, age=timedelta(days=29))
    before = state()
    assert reconcile(old, "item", item_id, user_id) == "no_intent"
    assert state() == before
    assert reconcile(recent, "item", item_id, user_id) == "deleted"


def test_an_entity_already_gone_is_absent_and_its_intent_removed(db_client, signup):
    headers, user_id, item_id, _ = wardrobe(db_client, signup)
    assert db_client.delete(f"/items/{item_id}", headers=headers).status_code == 204
    entry = intent("item", item_id, user_id)
    assert reconcile(entry, "item", item_id, user_id, dry_run=True) == "absent"
    assert len(state()["deletion_intents"]) == 1  # a dry run keeps it
    assert reconcile(entry, "item", item_id, user_id) == "absent"
    assert state()["deletion_intents"] == []


def test_db12_a_half_done_deletion_is_completed(db_client, signup, monkeypatch):
    # The server wrote the entry and the intent, then the deletion failed (#52's reverse case).
    headers, user_id, item_id, photo_id = wardrobe(db_client, signup)
    real = deletion.apply_deletion

    def apply_then_fail(db, *args):
        real(db, *args)
        raise RuntimeError("connection lost before commit")

    monkeypatch.setattr(deletion, "apply_deletion", apply_then_fail)
    with pytest.raises(RuntimeError):
        db_client.delete(f"/items/{item_id}", headers=headers)
    monkeypatch.undo()
    ((entry_id, entity_type, entity_id, intent_user, _),) = state()["deletion_intents"]
    assert (entity_type, entity_id, intent_user) == ("item", item_id, user_id)
    assert reconcile(entry_id, "item", item_id, user_id) == "deleted"
    assert db_client.get(f"/items/{item_id}", headers=headers).status_code == 404


def test_a_server_deletion_leaves_no_intent_for_the_reconciler(db_client, signup):
    headers, user_id, item_id, _ = wardrobe(db_client, signup)
    assert db_client.delete(f"/items/{item_id}", headers=headers).status_code == 204
    assert state()["deletion_intents"] == []


# ------------------------------------------------------------------ reconcile_record_run

def test_a_run_is_recorded_with_counts_only(db_client):
    started = clock.now() - timedelta(minutes=2)
    with connect(E.RECONCILER) as conn:
        conn.execute("SELECT dolab.reconcile_record_run(%s, now(), 10, 9, 1, 3, 2, 1, 2, 0, 0)", (started,))
        for bad in ("SELECT dolab.reconcile_record_run(%s, now(), -1, 0, 0, 0, 0, 0, 0, 0, 0)",
                    "SELECT dolab.reconcile_record_run(now(), %s, 0, 0, 0, 0, 0, 0, 0, 0, 0)",
                    "SELECT dolab.reconcile_record_run(%s, now(), NULL, 0, 0, 0, 0, 0, 0, 0, 0)"):
            with pytest.raises(errors.InvalidParameterValue):
                conn.execute(bad, (started,))
    with connect() as admin:
        ((actor, action, outcome, counts),) = rows(admin, "SELECT actor, action, outcome, counts FROM dolab.audit_log")
    assert (actor, action, outcome) == ("reconciler", "reconcile.run", "recorded")
    assert {k: v for k, v in counts.items() if k not in ("started", "finished")} == {
        "read": 10, "valid": 9, "bad_signature": 1, "deleted": 3, "already_done": 2, "absent": 1,
        "no_intent": 2, "owner_mismatch": 0, "errors": 0}


# ------------------------------------------------------------------ parity: Python (SQLite) and PostgreSQL

@pytest.mark.parametrize("what", ["item", "photo", "user"])
def test_the_python_and_postgresql_deletions_do_the_same(db_client, signup, what):
    """Both logics on the same data: the same rows left in every table, the same file keys."""
    _, user_id, item_id, photo_id = wardrobe(db_client, signup)
    wardrobe(db_client, signup, "other@example.com")
    entity = {"item": item_id, "photo": photo_id, "user": user_id}[what]
    results = {}
    for name, logic in (("python", deletion.python_deletion), ("postgresql", deletion.function_deletion)):
        with SessionLocal() as db:
            keys = logic(db, what, entity, user_id)
            snapshot = {}
            for table in STATE_TABLES[:-3]:  # what the API role can read
                snapshot[table] = sorted(map(tuple, db.execute(text(f"SELECT * FROM dolab.{table}")).all()), key=repr)
            db.rollback()
        results[name] = (sorted(keys), snapshot)
    assert results["python"][0] and results["python"][0] == results["postgresql"][0]
    assert results["python"][1] == results["postgresql"][1]


def test_both_logics_refuse_someone_elses_entity(db_client, signup):
    _, mine, _, _ = wardrobe(db_client, signup)
    _, _, their_item, their_photo = wardrobe(db_client, signup, "other@example.com")
    for what, entity in (("item", their_item), ("photo", their_photo), ("user", their_item)):
        with SessionLocal() as db:
            with pytest.raises(deletion.NotFound):
                deletion.python_deletion(db, what, entity, mine)
        with SessionLocal() as db:
            with pytest.raises(Exception, match="no such"):
                deletion.function_deletion(db, what, entity, mine)
    with SessionLocal() as db:
        with pytest.raises(Exception, match="unknown entity type"):
            deletion.function_deletion(db, "profile", their_item, mine)
