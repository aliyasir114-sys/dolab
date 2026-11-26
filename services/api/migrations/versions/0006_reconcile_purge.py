"""Reconciliation job support (plan 4.10.1ب; step C, deletion ledger part 4; notice #57).

- dolab.reconcile_purge_intents(dry_run): deletes intents older than 31 days, and only those.
  The age is fixed here, not a parameter, so a caller cannot shorten it. Writes one audit row
  with the count only (not on a dry run). The reconciliation job calls it only after a run in
  which the ledger was listed in full and every entry was valid and processed (Ali, 2026-11-26).
- dolab.reconcile_record_run: same signature as in 0005; the run is now recorded as 'failed'
  when any entry was invalid, any error happened (a failed ledger listing counts as an error)
  or any owner mismatch was found, and as 'recorded' otherwise.

Both SECURITY DEFINER, owned by dolab_deleter, search_path pinned, no EXECUTE for PUBLIC; only
dolab_reconciler may run them. dolab_deleter needs no new privilege: it already reads
deletion_intents.created_at and deletes intents (0005). dolab_reconciler still has no privilege
on any table. The same privileges are listed in dbtools/expected_acl.py.
"""
import importlib.util
from pathlib import Path

from alembic import op

from dbtools.deleter_functions import replace_deleter_function

revision = "0006_reconcile_purge"
down_revision = "0005_deleter_functions"
branch_labels = None
depends_on = None

PURGE = r"""
CREATE OR REPLACE FUNCTION dolab.reconcile_purge_intents(p_dry_run boolean)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, dolab
AS $fn$
DECLARE
    v_count integer;
BEGIN
    IF p_dry_run IS NULL THEN
        RAISE EXCEPTION 'reconcile_purge_intents: dry_run is required' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_dry_run THEN
        SELECT count(*) INTO v_count FROM dolab.deletion_intents
        WHERE created_at < now() - interval '31 days';
        RETURN v_count;
    END IF;
    DELETE FROM dolab.deletion_intents WHERE created_at < now() - interval '31 days';
    GET DIAGNOSTICS v_count = ROW_COUNT;
    INSERT INTO dolab.audit_log (id, at, actor, action, outcome, counts)
    VALUES (gen_random_uuid()::text, now(), 'reconciler', 'reconcile.purge_intents', 'purged',
            json_build_object('purged', v_count));
    RETURN v_count;
END
$fn$
"""

PURGE_SIGNATURE = "dolab.reconcile_purge_intents(boolean)"
RECORD_SIGNATURE = ("dolab.reconcile_record_run(timestamptz, timestamptz, integer, integer, integer, integer, "
                    "integer, integer, integer, integer, integer)")


def _migration_0005():
    path = Path(__file__).with_name("0005_deleter_functions.py")
    spec = importlib.util.spec_from_file_location("dolab_migration_0005", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RECORD_RUN_0005 = _migration_0005().RECORD_RUN
_RECORDED = "'reconcile.run', 'recorded',"
assert RECORD_RUN_0005.count(_RECORDED) == 1
RECORD_RUN = RECORD_RUN_0005.replace(
    _RECORDED,
    "'reconcile.run',\n            CASE WHEN p_bad > 0 OR p_errors > 0 OR p_owner_mismatch > 0 THEN 'failed' ELSE 'recorded' END,",
)


def upgrade() -> None:
    replace_deleter_function(op.execute, PURGE, PURGE_SIGNATURE, ["dolab_reconciler"])
    replace_deleter_function(op.execute, RECORD_RUN, RECORD_SIGNATURE, ["dolab_reconciler"])


def downgrade() -> None:
    replace_deleter_function(op.execute, RECORD_RUN_0005, RECORD_SIGNATURE, ["dolab_reconciler"])
    op.execute("SET ROLE dolab_deleter")
    op.execute(f"DROP FUNCTION IF EXISTS {PURGE_SIGNATURE}")
    op.execute("SET ROLE dolab_schema_owner")
