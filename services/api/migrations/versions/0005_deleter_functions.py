"""Deleter functions (plan 4.10.1د; step C, deletion ledger part 3; notice #55).

- dolab.apply_deletion: the one deletion logic for an item, a photo or an account, used by the
  API (on PostgreSQL) and by reconcile_apply. Returns the photo file keys to delete.
- dolab.reconcile_apply: completes a deletion the server started (a matching intent, at most
  30 days old) and could not finish. The only way the reconciliation job can delete anything.
- dolab.reconcile_record_run: one audit row per reconciliation run, counts only.

All three are SECURITY DEFINER, owned by dolab_deleter (NOLOGIN), with search_path pinned to
pg_catalog, dolab, fully qualified names, no dynamic SQL, and no EXECUTE for PUBLIC. They are
written as dolab_deleter by dbtools.deleter_functions (temporary CREATE inside this migration's
transaction only).

dolab_deleter gets only what the functions need: SELECT on the columns they read, DELETE on the
user data tables and deletion_intents, INSERT on reconciled_entries and audit_log, and UPDATE on
users.updated_at so that it may lock a user's row (SELECT ... FOR UPDATE needs UPDATE on one
column). The functions never write that column; step D moves this grant to users.change_seq.

Not here (Ali, 2026-11-26): sync tombstones (step D, to be added inside apply_deletion) and the
pending_file_deletions queue (its own request). Photo files of deletions completed by
reconcile_apply are left for the orphan job, which must therefore run on a schedule wherever
reconciliation is enabled.

The same privileges are listed in dbtools/expected_acl.py.
"""
import sqlalchemy as sa
from alembic import op

from dbtools.deleter_functions import replace_deleter_function

revision = "0005_deleter_functions"
down_revision = "0004_deletion_intents"
branch_labels = None
depends_on = None

S = "dolab"

APPLY_DELETION = r"""
CREATE OR REPLACE FUNCTION dolab.apply_deletion(p_entity_type text, p_entity_id uuid, p_user_id uuid)
RETURNS SETOF text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, dolab
AS $fn$
DECLARE
    v_entity text := p_entity_id::text;
    v_user text := p_user_id::text;
BEGIN
    IF p_entity_type IS NULL OR p_entity_id IS NULL OR p_user_id IS NULL THEN
        RAISE EXCEPTION 'apply_deletion: arguments must not be null' USING ERRCODE = 'null_value_not_allowed';
    END IF;
    CASE p_entity_type
    WHEN 'item' THEN
        IF NOT EXISTS (SELECT 1 FROM dolab.items i WHERE i.id = v_entity AND i.user_id = v_user) THEN
            RAISE EXCEPTION 'apply_deletion: no such item for this user' USING ERRCODE = 'no_data_found';
        END IF;
        RETURN QUERY
            SELECT k.key::text FROM dolab.item_photos p
            CROSS JOIN LATERAL (VALUES (p.original_key), (p.thumb_key)) AS k(key)
            WHERE p.item_id = v_entity;
        DELETE FROM dolab.wear_logs WHERE item_id = v_entity;
        DELETE FROM dolab.item_status_changes WHERE item_id = v_entity;
        DELETE FROM dolab.donation_items WHERE item_id = v_entity;
        DELETE FROM dolab.item_photos WHERE item_id = v_entity;
        DELETE FROM dolab.items WHERE id = v_entity;
    WHEN 'photo' THEN
        IF NOT EXISTS (SELECT 1 FROM dolab.item_photos p WHERE p.id = v_entity AND p.user_id = v_user) THEN
            RAISE EXCEPTION 'apply_deletion: no such photo for this user' USING ERRCODE = 'no_data_found';
        END IF;
        RETURN QUERY
            SELECT k.key::text FROM dolab.item_photos p
            CROSS JOIN LATERAL (VALUES (p.original_key), (p.thumb_key)) AS k(key)
            WHERE p.id = v_entity;
        DELETE FROM dolab.item_photos WHERE id = v_entity;
    WHEN 'user' THEN
        IF v_entity <> v_user OR NOT EXISTS (SELECT 1 FROM dolab.users u WHERE u.id = v_user) THEN
            RAISE EXCEPTION 'apply_deletion: no such account' USING ERRCODE = 'no_data_found';
        END IF;
        RETURN QUERY
            SELECT k.key::text FROM dolab.item_photos p
            CROSS JOIN LATERAL (VALUES (p.original_key), (p.thumb_key)) AS k(key)
            WHERE p.user_id = v_user;
        DELETE FROM dolab.item_photos WHERE user_id = v_user;
        DELETE FROM dolab.donation_items
            WHERE donation_id IN (SELECT d.id FROM dolab.donations d WHERE d.user_id = v_user);
        DELETE FROM dolab.donations WHERE user_id = v_user;
        DELETE FROM dolab.wear_logs WHERE user_id = v_user;
        DELETE FROM dolab.item_status_changes WHERE user_id = v_user;
        DELETE FROM dolab.items WHERE user_id = v_user;
        DELETE FROM dolab.profiles WHERE user_id = v_user;
        DELETE FROM dolab.refresh_tokens WHERE user_id = v_user;
        DELETE FROM dolab.password_reset_tokens WHERE user_id = v_user;
        DELETE FROM dolab.users WHERE id = v_user;
    ELSE
        RAISE EXCEPTION 'apply_deletion: unknown entity type' USING ERRCODE = 'invalid_parameter_value';
    END CASE;
    -- Step D adds the sync tombstone here, for the server and the reconciliation job alike.
    RETURN;
END
$fn$
"""

RECONCILE_APPLY = r"""
CREATE OR REPLACE FUNCTION dolab.reconcile_apply(
    p_entry_id uuid, p_entity_type text, p_entity_id uuid, p_user_id uuid, p_dry_run boolean)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, dolab
AS $fn$
DECLARE
    v_entry text := p_entry_id::text;
    v_entity text := p_entity_id::text;
    v_user text := p_user_id::text;
    v_owner text;
BEGIN
    IF p_entry_id IS NULL OR p_entity_id IS NULL OR p_user_id IS NULL OR p_dry_run IS NULL THEN
        RAISE EXCEPTION 'reconcile_apply: arguments must not be null' USING ERRCODE = 'null_value_not_allowed';
    END IF;
    IF p_entity_type IS NULL OR p_entity_type NOT IN ('item', 'photo', 'user') THEN
        RAISE EXCEPTION 'reconcile_apply: unknown entity type' USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- 1. Done before.
    IF EXISTS (SELECT 1 FROM dolab.reconciled_entries r WHERE r.entry_id = v_entry) THEN
        RETURN 'already_done';
    END IF;

    -- 2. An intent the server recorded, matching all four fields, at most 30 days old.
    IF NOT EXISTS (
        SELECT 1 FROM dolab.deletion_intents d
        WHERE d.entry_id = v_entry AND d.entity_type = p_entity_type
          AND d.entity_id = v_entity AND d.user_id = v_user
          AND d.created_at > now() - interval '30 days'
    ) THEN
        RETURN 'no_intent';
    END IF;

    -- 3. The user's row first, the same order as every other write on the user's data.
    PERFORM 1 FROM dolab.users u WHERE u.id = v_user FOR UPDATE;

    -- 4. The entity, and its owner.
    CASE p_entity_type
    WHEN 'item' THEN SELECT i.user_id INTO v_owner FROM dolab.items i WHERE i.id = v_entity;
    WHEN 'photo' THEN SELECT p.user_id INTO v_owner FROM dolab.item_photos p WHERE p.id = v_entity;
    ELSE SELECT u.id INTO v_owner FROM dolab.users u WHERE u.id = v_entity;
    END CASE;
    IF NOT FOUND THEN
        IF NOT p_dry_run THEN
            DELETE FROM dolab.deletion_intents WHERE entry_id = v_entry;
        END IF;
        RETURN 'absent';
    END IF;

    -- 5. It belongs to that user (an account is its own owner).
    IF v_owner <> v_user OR (p_entity_type = 'user' AND v_entity <> v_user) THEN
        IF NOT p_dry_run THEN
            INSERT INTO dolab.audit_log (id, at, actor, action, entry_id, entity_type, outcome)
            VALUES (gen_random_uuid()::text, now(), 'reconciler', 'reconcile.entry', v_entry, p_entity_type,
                    'owner_mismatch');
        END IF;
        RETURN 'owner_mismatch';
    END IF;

    -- 6. A dry run changes nothing.
    IF p_dry_run THEN
        RETURN 'would_delete';
    END IF;

    -- 7. The one deletion logic. Photo files are left for the orphan job (no queue yet).
    PERFORM dolab.apply_deletion(p_entity_type, p_entity_id, p_user_id);
    DELETE FROM dolab.deletion_intents WHERE entry_id = v_entry;
    INSERT INTO dolab.reconciled_entries (entry_id, reconciled_at) VALUES (v_entry, now());
    INSERT INTO dolab.audit_log (id, at, actor, action, entry_id, entity_type, outcome)
    VALUES (gen_random_uuid()::text, now(), 'reconciler', 'reconcile.entry', v_entry, p_entity_type, 'deleted');
    RETURN 'deleted';
END
$fn$
"""

RECORD_RUN = r"""
CREATE OR REPLACE FUNCTION dolab.reconcile_record_run(
    p_started timestamptz, p_finished timestamptz, p_read integer, p_valid integer, p_bad integer,
    p_deleted integer, p_already integer, p_absent integer, p_no_intent integer, p_owner_mismatch integer,
    p_errors integer)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, dolab
AS $fn$
BEGIN
    IF p_started IS NULL OR p_finished IS NULL OR p_finished < p_started OR p_finished > now() + interval '5 minutes' THEN
        RAISE EXCEPTION 'reconcile_record_run: bad run times' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF least(p_read, p_valid, p_bad, p_deleted, p_already, p_absent, p_no_intent, p_owner_mismatch, p_errors) < 0
       OR num_nulls(p_read, p_valid, p_bad, p_deleted, p_already, p_absent, p_no_intent, p_owner_mismatch, p_errors) > 0 THEN
        RAISE EXCEPTION 'reconcile_record_run: counts must be whole numbers, zero or more'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO dolab.audit_log (id, at, actor, action, outcome, counts)
    VALUES (gen_random_uuid()::text, now(), 'reconciler', 'reconcile.run', 'recorded',
            json_build_object('started', p_started, 'finished', p_finished, 'read', p_read, 'valid', p_valid,
                               'bad_signature', p_bad, 'deleted', p_deleted, 'already_done', p_already,
                               'absent', p_absent, 'no_intent', p_no_intent,
                               'owner_mismatch', p_owner_mismatch, 'errors', p_errors));
END
$fn$
"""

# The columns dolab_deleter may read: identifiers, owners and file keys, nothing else.
DELETER_COLUMNS = {
    "users": ["id"],
    "items": ["id", "user_id"],
    "item_photos": ["id", "item_id", "user_id", "original_key", "thumb_key"],
    "wear_logs": ["item_id", "user_id"],
    "item_status_changes": ["item_id", "user_id"],
    "donation_items": ["item_id", "donation_id"],
    "donations": ["id", "user_id"],
    "profiles": ["user_id"],
    "refresh_tokens": ["user_id"],
    "password_reset_tokens": ["user_id"],
    "deletion_intents": ["entry_id", "entity_type", "entity_id", "user_id", "created_at"],
    "reconciled_entries": ["entry_id"],
}
DELETER_DELETE = ["users", "items", "item_photos", "wear_logs", "item_status_changes", "donation_items",
                  "donations", "profiles", "refresh_tokens", "password_reset_tokens", "deletion_intents"]
DELETER_INSERT = ["reconciled_entries", "audit_log"]


def upgrade() -> None:
    op.create_table(
        "reconciled_entries",
        sa.Column("entry_id", sa.String(36), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("entry_id", name="pk_reconciled_entries"),
        schema=S,
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(20), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("entry_id", sa.String(36), nullable=True),
        sa.Column("entity_type", sa.String(10), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=True),
        sa.Column("counts", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
        schema=S,
    )
    op.create_index("ix_audit_log_at", "audit_log", ["at"], schema=S)
    # The tables are the schema owner's; these grants are made as the owner.
    op.execute("SET ROLE dolab_schema_owner")
    for table, columns in DELETER_COLUMNS.items():
        op.execute(f"GRANT SELECT ({', '.join(columns)}) ON TABLE dolab.{table} TO dolab_deleter")
    for table in DELETER_DELETE:
        op.execute(f"GRANT DELETE ON TABLE dolab.{table} TO dolab_deleter")
    for table in DELETER_INSERT:
        op.execute(f"GRANT INSERT ON TABLE dolab.{table} TO dolab_deleter")
    op.execute("GRANT UPDATE (updated_at) ON TABLE dolab.users TO dolab_deleter")
    replace_deleter_function(op.execute, APPLY_DELETION, "dolab.apply_deletion(text, uuid, uuid)",
                             ["dolab_app"])
    replace_deleter_function(op.execute, RECONCILE_APPLY,
                             "dolab.reconcile_apply(uuid, text, uuid, uuid, boolean)", ["dolab_reconciler"])
    replace_deleter_function(
        op.execute, RECORD_RUN,
        "dolab.reconcile_record_run(timestamptz, timestamptz, integer, integer, integer, integer, integer, "
        "integer, integer, integer, integer)",
        ["dolab_reconciler"],
    )


def downgrade() -> None:
    op.execute("SET ROLE dolab_deleter")
    op.execute("DROP FUNCTION IF EXISTS dolab.reconcile_record_run(timestamptz, timestamptz, integer, integer, "
               "integer, integer, integer, integer, integer, integer, integer)")
    op.execute("DROP FUNCTION IF EXISTS dolab.reconcile_apply(uuid, text, uuid, uuid, boolean)")
    op.execute("DROP FUNCTION IF EXISTS dolab.apply_deletion(text, uuid, uuid)")
    op.execute("SET ROLE dolab_schema_owner")
    for table in DELETER_COLUMNS:
        op.execute(f"REVOKE ALL ON TABLE dolab.{table} FROM dolab_deleter")
    op.drop_table("audit_log", schema=S)
    op.drop_table("reconciled_entries", schema=S)
