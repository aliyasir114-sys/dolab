"""PostgreSQL roles, ownership and privileges (plan 4.10.1د, decision 20): DB13–DB23 and the
dolab_app privilege matrix.

Runs only when DOLAB_TEST_PG_ADMIN_URL points at a PostgreSQL database that has been set up with
dbtools/bootstrap.sql and migrated to head (the CI PostgreSQL job does this). The login roles
use the password in DOLAB_TEST_PG_PASSWORD. Some tests build a throwaway database of their own.
"""
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

ADMIN_URL = os.getenv("DOLAB_TEST_PG_ADMIN_URL", "")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="needs PostgreSQL (DOLAB_TEST_PG_ADMIN_URL)")

if ADMIN_URL:
    import psycopg
    from psycopg import errors
    from sqlalchemy.engine import make_url

from dbtools import expected_acl as E  # noqa: E402
from dbtools.deleter_functions import replace_deleter_function  # noqa: E402

API_DIR = Path(__file__).resolve().parent.parent
BOOTSTRAP_SQL = API_DIR / "dbtools" / "bootstrap.sql"
PASSWORD = os.getenv("DOLAB_TEST_PG_PASSWORD", "")
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


# ---------------------------------------------------------------- connections

def _conninfo(role: str | None = None, database: str | None = None) -> str:
    url = make_url(ADMIN_URL).set(drivername="postgresql")
    if role is not None:
        url = url.set(username=role, password=PASSWORD)
    if database is not None:
        url = url.set(database=database)
    return url.render_as_string(hide_password=False)


def _sqlalchemy_url(role: str, database: str) -> str:
    url = make_url(ADMIN_URL).set(drivername="postgresql+psycopg", username=role, password=PASSWORD, database=database)
    return url.render_as_string(hide_password=False)


@contextmanager
def connect(role: str | None = None, database: str | None = None, autocommit: bool = True):
    conn = psycopg.connect(_conninfo(role, database), autocommit=autocommit)
    try:
        yield conn
    finally:
        conn.close()


def main_db() -> str:
    return make_url(ADMIN_URL).database


def rows(conn, sql: str, *params) -> list[tuple]:
    return conn.execute(sql, params or None).fetchall()


def value(conn, sql: str, *params):
    return conn.execute(sql, params or None).fetchone()[0]


def denied(conn, sql: str) -> bool:
    """True if PostgreSQL refuses the statement for lack of privilege (SQLSTATE 42501).
    Any other error is raised, so a typo cannot pass as a refusal."""
    try:
        conn.execute(sql)
    except errors.InsufficientPrivilege:
        if conn.info.transaction_status == psycopg.pq.TransactionStatus.INERROR:
            conn.rollback()
        return True
    return False


def _bootstrap_sql() -> str:
    # psql meta-commands (lines starting with a backslash) are not SQL.
    return "\n".join(line for line in BOOTSTRAP_SQL.read_text().splitlines() if not line.startswith("\\"))


@pytest.fixture
def scratch_db():
    """A new, empty database prepared with bootstrap.sql only (no migrations)."""
    name = f"dolab_scratch_{uuid.uuid4().hex[:10]}"
    with connect() as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        with connect(database=name) as admin:
            admin.execute(_bootstrap_sql())
        yield name
    finally:
        with connect() as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


# ---------------------------------------------------------------- catalog helpers

_ACL_SQL = """
    SELECT obj, CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
           a.privilege_type, a.is_grantable
    FROM ({objects}) o, aclexplode(coalesce(o.acl, acldefault(o.kind, o.owner))) a
    WHERE a.grantee <> o.owner
"""
# Objects that belong to an extension (pg_depend deptype 'e') are not application objects (DB17).
_NOT_EXTENSION = """
      AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = '{catalog}'::regclass
                      AND d.objid = {oid} AND d.deptype = 'e')
"""
_TABLES = """
    SELECT c.relname AS obj, c.relacl AS acl, 'r'::"char" AS kind, c.relowner AS owner
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'dolab' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
""" + _NOT_EXTENSION.format(catalog="pg_class", oid="c.oid")
_SEQUENCES = """
    SELECT c.relname AS obj, c.relacl AS acl, 's'::"char" AS kind, c.relowner AS owner
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'dolab' AND c.relkind = 'S'
""" + _NOT_EXTENSION.format(catalog="pg_class", oid="c.oid")
_FUNCTIONS = """
    SELECT p.oid::regprocedure::text AS obj, p.proacl AS acl, 'f'::"char" AS kind, p.proowner AS owner
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'dolab'
""" + _NOT_EXTENSION.format(catalog="pg_proc", oid="p.oid")
# Standalone types only: not the row type of a table and not an array type.
_TYPES = """
    SELECT t.typname AS obj, t.typacl AS acl, 'T'::"char" AS kind, t.typowner AS owner
    FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
    LEFT JOIN pg_class c ON c.oid = t.typrelid
    WHERE n.nspname = 'dolab' AND t.typcategory <> 'A' AND (t.typrelid = 0 OR c.relkind = 'c')
""" + _NOT_EXTENSION.format(catalog="pg_type", oid="t.oid")


def acl(conn, objects_sql: str) -> dict[str, dict[str, set[str]]]:
    """object -> grantee -> privileges, leaving out the owner's own rights. Fails on GRANT OPTION."""
    found: dict[str, dict[str, set[str]]] = {}
    for (obj,) in rows(conn, f"SELECT obj FROM ({objects_sql}) o"):
        found[obj] = {}
    for obj, grantee, privilege, grantable in rows(conn, _ACL_SQL.format(objects=objects_sql)):
        assert not grantable, f"{grantee} may re-grant {privilege} on {obj}"
        found.setdefault(obj, {}).setdefault(grantee, set()).add(privilege)
    return found


def owners(conn, objects_sql: str) -> dict[str, str]:
    return {obj: role for obj, role in rows(conn, f"SELECT obj, pg_get_userbyid(owner) FROM ({objects_sql}) o")}


# ---------------------------------------------------------------- roles and memberships

def test_role_attributes():
    with connect() as conn:
        found = {}
        for name, login, inherit, limit, *system in rows(
            conn,
            "SELECT rolname, rolcanlogin, rolinherit, rolconnlimit, rolsuper, rolcreatedb, "
            "rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname = ANY(%s)",
            list(E.DOLAB_ROLES),
        ):
            assert not any(system), f"{name} has a system attribute"
            found[name] = (login, inherit, limit)
    assert found == E.ROLE_ATTRIBUTES


def test_db18_memberships():
    """Only dolab_deploy is a member of anything: SET to owner and deleter, no INHERIT, no ADMIN."""
    allowed_extra = {r for r in os.getenv("DOLAB_TEST_PG_BOOTSTRAP_ROLES", "").split(",") if r}
    with connect() as conn:
        memberships = rows(
            conn,
            "SELECT g.rolname, u.rolname, am.inherit_option, am.set_option, am.admin_option "
            "FROM pg_auth_members am JOIN pg_roles g ON g.oid = am.roleid JOIN pg_roles u ON u.oid = am.member "
            "WHERE g.rolname = ANY(%s) OR u.rolname = ANY(%s)",
            list(E.DOLAB_ROLES),
            list(E.DOLAB_ROLES),
        )
        found = {(g, u): (i, s, a) for g, u, i, s, a in memberships if u not in allowed_extra}
        assert found == E.MEMBERSHIPS
        for role in (E.APP, E.RECONCILER):
            for target in (E.OWNER, E.DELETER):
                assert value(conn, "SELECT pg_has_role(%s, %s, 'MEMBER')", role, target) is False
                assert value(conn, "SELECT pg_has_role(%s, %s, 'SET')", role, target) is False
        assert value(conn, "SELECT pg_has_role(%s, %s, 'USAGE')", E.DEPLOY, E.OWNER) is False


def test_db14_operating_roles_cannot_create_or_switch():
    for role in (E.APP, E.RECONCILER):
        with connect(role) as conn:
            assert denied(conn, "CREATE TABLE dolab.dz_probe (i int)")
            assert denied(conn, "CREATE FUNCTION dolab.dz_probe() RETURNS int LANGUAGE sql AS 'SELECT 1'")
            assert denied(conn, f"SET ROLE {E.OWNER}")
            assert denied(conn, f"SET ROLE {E.DELETER}")
            assert denied(conn, "CREATE TEMP TABLE dz_probe (i int)")
            assert denied(conn, "CREATE TABLE public.dz_probe (i int)")
    with connect() as conn:
        assert value(conn, "SELECT rolcanlogin FROM pg_roles WHERE rolname = %s", E.DELETER) is False
    # dolab_deleter cannot log in; reach it the only way there is, through dolab_deploy.
    with connect(E.DEPLOY) as conn:
        conn.execute(f"SET ROLE {E.DELETER}")
        assert denied(conn, "CREATE TABLE dolab.dz_probe (i int)")
        assert denied(conn, "CREATE FUNCTION dolab.dz_probe() RETURNS int LANGUAGE sql AS 'SELECT 1'")


def test_deploy_has_no_rights_of_its_own():
    with connect(E.DEPLOY) as conn:
        assert denied(conn, "CREATE TABLE dolab.dz_probe (i int)")
        assert denied(conn, "SELECT 1 FROM dolab.users")


# ---------------------------------------------------------------- schema and objects

def test_db13_schema_privileges():
    with connect() as conn:
        owner, acl_items = rows(
            conn, "SELECT pg_get_userbyid(nspowner), nspacl FROM pg_namespace WHERE nspname = 'dolab'"
        )[0]
        assert owner == E.OWNER
        found: dict[str, set[str]] = {}
        for grantee, privilege, grantable in rows(
            conn,
            "SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END, "
            "a.privilege_type, a.is_grantable FROM pg_namespace n, aclexplode(n.nspacl) a WHERE n.nspname = 'dolab'",
        ):
            assert not grantable
            found.setdefault(grantee, set()).add(privilege)
        assert found == E.SCHEMA_ACL
        for role in (*E.OPERATING_ROLES, E.DEPLOY):
            assert value(conn, "SELECT has_schema_privilege(%s, 'dolab', 'CREATE')", role) is False


def test_db17_inventory_tables_match_expected_list():
    """Every table in dolab is classified, owned by the schema owner, and has exactly the listed
    privileges; no column-level grants and no GRANT OPTION anywhere."""
    with connect() as conn:
        found = acl(conn, _TABLES)
        assert set(found) == set(E.TABLES), "unclassified or missing tables"
        for table, expected in E.TABLES.items():
            assert found[table] == expected, table
        assert set(owners(conn, _TABLES).values()) == {E.OWNER}
        column_grants = rows(
            conn,
            "SELECT c.relname, a.attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'dolab' AND a.attacl IS NOT NULL",
        )
        assert column_grants == []


def test_app_privilege_matrix_effective():
    """The privileges each role actually has (has_table_privilege), not only what the ACL says."""
    with connect() as conn:
        for table, expected in E.TABLES.items():
            for role in (*E.OPERATING_ROLES, E.DEPLOY, "public"):
                have = {
                    p for p in PRIVILEGES
                    if value(conn, "SELECT has_table_privilege(%s, %s, %s)", role, f"dolab.{table}", p)
                }
                assert have == expected.get(role, set()), (role, table)


def test_app_cannot_truncate_or_touch_version_table():
    with connect(E.APP) as conn:
        assert denied(conn, "TRUNCATE dolab.items")
        assert denied(conn, "SELECT * FROM dolab.alembic_version")
        assert denied(conn, "UPDATE dolab.item_photos SET width = width")


def test_db15_sequences():
    with connect() as conn:
        assert acl(conn, _SEQUENCES) == E.SEQUENCES


def test_functions_and_db21_types_match_expected_list():
    with connect() as conn:
        assert acl(conn, _FUNCTIONS) == E.FUNCTIONS
        assert acl(conn, _TYPES) == E.TYPES


def test_default_privileges():
    with connect() as conn:
        found = {}
        for creator, kind, acl_text in rows(
            conn,
            "SELECT pg_get_userbyid(d.defaclrole), d.defaclobjtype, d.defaclacl::text FROM pg_default_acl d "
            "WHERE d.defaclnamespace = 0 AND pg_get_userbyid(d.defaclrole) = ANY(%s)",
            list(E.DOLAB_ROLES),
        ):
            grantees = {}
            for grantee, privilege in rows(
                conn,
                "SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END, a.privilege_type "
                "FROM aclexplode(%s::aclitem[]) a WHERE a.grantee <> (SELECT oid FROM pg_roles WHERE rolname = %s)",
                acl_text,
                creator,
            ):
                grantees.setdefault(grantee, set()).add(privilege)
            found[(creator, kind)] = grantees
        assert found == E.DEFAULT_ACL
        # No schema-scoped default grants either.
        assert rows(conn, "SELECT 1 FROM pg_default_acl WHERE defaclnamespace <> 0") == []


def test_db16_new_objects_get_no_default_grants(scratch_db):
    """Objects made by each production creator in a fresh database: nothing for PUBLIC or the
    operating roles until a migration grants it."""
    with connect(E.DEPLOY, scratch_db, autocommit=False) as conn:
        conn.execute(f"SET ROLE {E.OWNER}")
        conn.execute("CREATE TABLE dolab.t (i int)")
        conn.execute("CREATE SEQUENCE dolab.s")
        conn.execute("CREATE FUNCTION dolab.f() RETURNS int LANGUAGE sql AS 'SELECT 1'")
        conn.execute("CREATE TYPE dolab.ty AS ENUM ('a')")
        replace_deleter_function(
            conn.execute,
            "CREATE FUNCTION dolab.g() RETURNS int LANGUAGE sql SECURITY DEFINER "
            "SET search_path = pg_catalog, dolab AS 'SELECT 1'",
            "dolab.g()",
        )
        conn.execute(f"SET ROLE {E.OWNER}")
        conn.execute(f"GRANT CREATE ON SCHEMA dolab TO {E.DELETER}")
        conn.execute(f"SET ROLE {E.DELETER}")
        conn.execute("CREATE TABLE dolab.t2 (i int)")
        conn.execute("CREATE SEQUENCE dolab.s2")
        conn.execute("CREATE TYPE dolab.ty2 AS ENUM ('b')")
        conn.execute(f"SET ROLE {E.OWNER}")
        conn.execute(f"REVOKE CREATE ON SCHEMA dolab FROM {E.DELETER}")
        conn.commit()
    with connect(database=scratch_db) as conn:
        assert acl(conn, _TABLES) == {"t": {}, "t2": {}}
        assert acl(conn, _SEQUENCES) == {"s": {}, "s2": {}}
        assert owners(conn, _TABLES) == {"t": E.OWNER, "t2": E.DELETER}
        assert acl(conn, _FUNCTIONS) == {"dolab.f()": {}, "dolab.g()": {}}
        assert acl(conn, _TYPES) == {"ty": {}, "ty2": {}}
        # Every role other than the object's own creator: no rights at all.
        for table, sequence, creator in (("dolab.t", "dolab.s", E.OWNER), ("dolab.t2", "dolab.s2", E.DELETER)):
            for role in (*E.OPERATING_ROLES, "public"):
                if role == creator:
                    continue
                for p in PRIVILEGES:
                    assert value(conn, "SELECT has_table_privilege(%s, %s, %s)", role, table, p) is False
                for p in ("USAGE", "SELECT", "UPDATE"):
                    assert value(conn, "SELECT has_sequence_privilege(%s, %s, %s)", role, sequence, p) is False
        for role in (E.APP, E.RECONCILER, "public"):
            for function in ("dolab.f()", "dolab.g()"):
                assert value(conn, "SELECT has_function_privilege(%s, %s, 'EXECUTE')", role, function) is False
            for type_name in ("dolab.ty", "dolab.ty2"):
                assert value(conn, "SELECT has_type_privilege(%s, %s, 'USAGE')", role, type_name) is False


# ---------------------------------------------------------------- deleter functions

_PROBE = (
    "CREATE OR REPLACE FUNCTION dolab.dz_probe() RETURNS int LANGUAGE sql SECURITY DEFINER "
    "SET search_path = pg_catalog, dolab AS 'SELECT {n}'"
)


def _deleter_state(conn) -> tuple:
    return (
        value(conn, "SELECT dolab.dz_probe()"),
        value(conn, "SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid = 'dolab.dz_probe()'::regprocedure"),
        value(conn, "SELECT prosecdef FROM pg_proc WHERE oid = 'dolab.dz_probe()'::regprocedure"),
        value(conn, "SELECT proconfig::text FROM pg_proc WHERE oid = 'dolab.dz_probe()'::regprocedure"),
        value(conn, "SELECT has_function_privilege('public', 'dolab.dz_probe()', 'EXECUTE')"),
        value(conn, "SELECT has_function_privilege(%s, 'dolab.dz_probe()', 'EXECUTE')", E.RECONCILER),
        value(conn, "SELECT has_function_privilege(%s, 'dolab.dz_probe()', 'EXECUTE')", E.APP),
        value(conn, "SELECT has_schema_privilege(%s, 'dolab', 'CREATE')", E.DELETER),
    )


def _memberships(conn) -> list[tuple]:
    return rows(
        conn,
        "SELECT roleid::regrole::text, member::regrole::text, inherit_option, set_option, admin_option "
        "FROM pg_auth_members ORDER BY 1, 2",
    )


def test_db19_replace_deleter_function(scratch_db):
    for n in (1, 2):
        with connect(E.DEPLOY, scratch_db, autocommit=False) as conn:
            replace_deleter_function(conn.execute, _PROBE.format(n=n), "dolab.dz_probe()", [E.RECONCILER])
            conn.commit()
        with connect(database=scratch_db) as conn:
            assert _deleter_state(conn) == (
                n, E.DELETER, True, '{"search_path=pg_catalog, dolab"}', False, True, False, False
            )


def test_db20_failed_replacement_changes_nothing(scratch_db):
    with connect(E.DEPLOY, scratch_db, autocommit=False) as conn:
        replace_deleter_function(conn.execute, _PROBE.format(n=1), "dolab.dz_probe()", [E.RECONCILER])
        conn.commit()
    with connect(database=scratch_db) as conn:
        before, members_before = _deleter_state(conn), _memberships(conn)
    with connect(E.DEPLOY, scratch_db, autocommit=False) as conn:
        with pytest.raises(psycopg.Error):
            # Fails after the temporary GRANT CREATE and before the replacement completes.
            replace_deleter_function(conn.execute, _PROBE.format(n="1 +"), "dolab.dz_probe()", [E.RECONCILER])
        conn.rollback()
        assert value(conn, "SELECT current_user") == E.DEPLOY
    with connect(database=scratch_db) as conn:
        assert _deleter_state(conn) == before
        assert _memberships(conn) == members_before


def test_deleter_helper_refuses_unsafe_functions():
    with pytest.raises(ValueError):
        replace_deleter_function(print, "CREATE FUNCTION dolab.x() RETURNS int AS 'SELECT 1'", "dolab.x()")
    with pytest.raises(ValueError):
        replace_deleter_function(print, _PROBE.format(n=1), "dz_probe()")


# ---------------------------------------------------------------- migrations

def _alembic(scratch_db: str, monkeypatch):
    from alembic.config import Config

    monkeypatch.setenv("DOLAB_MIGRATION_URL", _sqlalchemy_url(E.DEPLOY, scratch_db))
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(API_DIR / "migrations"))
    return config


def test_db22_migrations_on_empty_database_as_deploy(scratch_db, monkeypatch):
    from alembic import command

    config = _alembic(scratch_db, monkeypatch)

    command.upgrade(config, "0001_baseline")
    with connect(database=scratch_db) as conn:
        assert value(conn, "SELECT to_regclass('dolab.alembic_version')::text") == "dolab.alembic_version"
        assert value(conn, "SELECT tableowner FROM pg_tables WHERE schemaname = 'dolab' AND tablename = 'alembic_version'") == E.OWNER
        assert value(conn, "SELECT version_num FROM dolab.alembic_version") == "0001_baseline"
        assert value(conn, "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'") == 0

    command.upgrade(config, "head")
    command.check(config)  # the models and the migrations agree
    with connect(database=scratch_db) as conn:
        assert value(conn, "SELECT version_num FROM dolab.alembic_version") == "0003_rate_limit_events"
        assert set(owners(conn, _TABLES).values()) == {E.OWNER}
        for table, expected in E.TABLES.items():
            assert acl(conn, _TABLES)[table] == expected, table
    with connect(E.DEPLOY, scratch_db) as conn:
        assert value(conn, "SELECT current_user") == E.DEPLOY
        assert value(conn, "SELECT has_schema_privilege(current_user, 'dolab', 'CREATE')") is False
        assert denied(conn, "CREATE TABLE dolab.dz_probe (i int)")

    # Every migration can be reversed, then applied again.
    command.downgrade(config, "base")
    with connect(database=scratch_db) as conn:
        assert set(acl(conn, _TABLES)) == {"alembic_version"}
    command.upgrade(config, "head")
    with connect(database=scratch_db) as conn:
        assert set(acl(conn, _TABLES)) == set(E.TABLES)


_SAMPLE_USER = "11111111-1111-4111-8111-111111111111"
_SAMPLE_ITEM = "22222222-2222-4222-8222-222222222222"


def _insert_sample_data(conn) -> None:
    """A user with an item, a wear and a status change, written as they were before migrating."""
    conn.execute(
        "INSERT INTO dolab.users (id, email, password_hash, locale, timezone, account_type, token_version, "
        "created_at, updated_at) VALUES (%s, 'old@example.com', 'scrypt$x', 'ar', 'Africa/Khartoum', "
        "'personal', 3, now(), now())", (_SAMPLE_USER,))
    conn.execute(
        "INSERT INTO dolab.items (id, user_id, name, category, primary_color, secondary_colors, "
        "color_confidence, color_confirmed, status, purchase_date, purchase_price, currency, added_at, "
        "updated_at, wear_count, last_worn_at) VALUES (%s, %s, 'جلابية بيضاء', 'jalabiya', 'white', "
        "'[\"grey\"]', 0.9, true, 'available', '2025-05-01', 45.50, 'USD', now(), now(), 1, '2026-11-01')",
        (_SAMPLE_ITEM, _SAMPLE_USER))
    conn.execute(
        "INSERT INTO dolab.wear_logs (id, user_id, item_id, worn_on) VALUES (%s, %s, %s, '2026-11-01')",
        (str(uuid.uuid4()), _SAMPLE_USER, _SAMPLE_ITEM))
    conn.execute(
        "INSERT INTO dolab.item_status_changes (id, item_id, user_id, from_status, to_status, reason, "
        "changed_at) VALUES (%s, %s, %s, NULL, 'available', 'created', now())",
        (str(uuid.uuid4()), _SAMPLE_ITEM, _SAMPLE_USER))


def _sample_data(conn) -> tuple:
    return (
        rows(conn, "SELECT email, locale, timezone, token_version FROM dolab.users"),
        rows(conn, "SELECT name, secondary_colors::text, purchase_price::text, wear_count, last_worn_at::text "
                   "FROM dolab.items"),
        value(conn, "SELECT count(*) FROM dolab.wear_logs"),
        value(conn, "SELECT count(*) FROM dolab.item_status_changes"),
    )


def test_migrations_keep_existing_data(scratch_db, monkeypatch):
    """A database already in use: data written at an earlier revision survives every later
    upgrade, a downgrade of the privileges migration and the upgrade back, and a bootstrap re-run."""
    from alembic import command

    config = _alembic(scratch_db, monkeypatch)
    command.upgrade(config, "0001_baseline")
    with connect(database=scratch_db) as conn:
        _insert_sample_data(conn)
        before = _sample_data(conn)
    assert before[0] == [("old@example.com", "ar", "Africa/Khartoum", 3)]
    with connect(E.APP, scratch_db) as conn:
        assert denied(conn, "SELECT email FROM dolab.users")  # no grants before 0002

    command.upgrade(config, "head")
    with connect(database=scratch_db) as conn:
        assert _sample_data(conn) == before
    with connect(E.APP, scratch_db) as conn:
        assert value(conn, "SELECT email FROM dolab.users") == "old@example.com"
        assert value(conn, "SELECT name FROM dolab.items") == "جلابية بيضاء"

    command.downgrade(config, "0001_baseline")
    with connect(database=scratch_db) as conn:
        assert _sample_data(conn) == before
    with connect(E.APP, scratch_db) as conn:
        assert denied(conn, "SELECT email FROM dolab.users")

    command.upgrade(config, "head")
    with connect(database=scratch_db) as conn:
        acl_before = acl(conn, _TABLES)
        conn.execute(_bootstrap_sql())  # re-run on a live, populated database
        assert _sample_data(conn) == before
        assert acl(conn, _TABLES) == acl_before
        for table, expected in E.TABLES.items():
            assert acl_before[table] == expected, table
    with connect(E.APP, scratch_db) as conn:
        assert value(conn, "SELECT count(*) FROM dolab.wear_logs") == 1


def test_baseline_refuses_tables_left_by_create_all(scratch_db, monkeypatch):
    """An old database made by create_all keeps its tables in public. The baseline stops instead
    of creating an empty second set beside them, and changes nothing."""
    from alembic import command

    with connect(database=scratch_db) as conn:
        conn.execute("CREATE TABLE public.users (id varchar(36) PRIMARY KEY, email varchar(254))")
        conn.execute("INSERT INTO public.users VALUES ('u1', 'kept@example.com')")
    config = _alembic(scratch_db, monkeypatch)
    with pytest.raises(RuntimeError, match="public.users"):
        command.upgrade(config, "head")
    with connect(database=scratch_db) as conn:
        assert value(conn, "SELECT to_regclass('dolab.alembic_version')") is None
        assert value(conn, "SELECT count(*) FROM pg_tables WHERE schemaname = 'dolab'") == 0
        assert value(conn, "SELECT email FROM public.users") == "kept@example.com"


def test_db23_api_connection():
    from sqlalchemy import select
    from sqlalchemy.dialects import postgresql

    from app.models import Item

    with connect(E.APP) as conn:
        assert value(conn, "SHOW search_path") == "pg_catalog, dolab"
        # The unqualified name resolves to dolab.items (regclass text drops a visible schema).
        assert value(
            conn, "SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.oid = to_regclass('items')"
        ) == "dolab"
        assert denied(conn, "CREATE TABLE items_copy (i int)")
    compiled = str(select(Item.id).compile(dialect=postgresql.dialect()))
    assert "FROM dolab.items" in compiled


def test_reconciler_session_settings():
    with connect(E.RECONCILER) as conn:
        assert value(conn, "SHOW search_path") == "pg_catalog, dolab"
        assert value(conn, "SHOW statement_timeout") == "30s"


def test_api_test_suite_runs_as_app_role():
    """The rest of the suite, in the PostgreSQL job, connects as dolab_app (not a superuser)."""
    from app.db import engine

    if engine.dialect.name != "postgresql":
        pytest.skip("SQLite run")
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT current_user").scalar() == E.APP
