"""The photo file deletion queue (plan 4.10.3, layer 2; notice #59, Ali's decisions 2026-11-26).

- Table dolab.pending_file_deletions: the storage keys of photo files whose database rows are
  deleted but whose files may still be in storage. Keys only; no user data besides what a key
  already carries (its user id prefix). last_error holds an error class name, never a message.
- dolab.apply_deletion (same signature as in 0005) now queues the photo file keys in the
  deletion's own transaction: one INSERT ... SELECT for all keys, duplicates dropped (DISTINCT,
  and ON CONFLICT DO NOTHING for a key already queued). The server and the reconciliation job both
  go through it, so neither path can commit a deletion without queueing its files. Files are
  deleted from storage only after the commit, by the server (the immediate attempt) and later by
  the queue job (python -m app.jobs.file_deletions, not scheduled anywhere yet). A new row is
  first due five minutes after the deletion (app.deletion.FIRST_TRY_DELAY on SQLite), so the job never takes a row
  while the server's immediate attempt is still working on it (review of #60, M1).
- Privileges (and nothing else on this table):
  dolab_deleter        INSERT (inside apply_deletion);
  dolab_app            DELETE, and SELECT on key only (it removes the rows of files it deleted);
  dolab_file_cleaner   SELECT and DELETE, and UPDATE on attempts, next_try_at, last_error and
                       lease_token only
                       (the queue job leases, reschedules and removes rows; it cannot change a
                       row's key or created_at; review of #60, M3).
  The downgrade refuses while the queue holds any row, so queued files are never dropped.
  dolab_file_cleaner has no privilege on any other table and runs no function (bootstrap.sql
  creates the role). The same privileges are listed in dbtools/expected_acl.py.
"""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import op

from dbtools.deleter_functions import replace_deleter_function

revision = "0007_file_deletion_queue"
down_revision = "0006_reconcile_purge"
branch_labels = None
depends_on = None

APPLY_SIGNATURE = "dolab.apply_deletion(text, uuid, uuid)"

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
    v_keys text[];
BEGIN
    IF p_entity_type IS NULL OR p_entity_id IS NULL OR p_user_id IS NULL THEN
        RAISE EXCEPTION 'apply_deletion: arguments must not be null' USING ERRCODE = 'null_value_not_allowed';
    END IF;
    CASE p_entity_type
    WHEN 'item' THEN
        -- Lock order users, then items, then photos, in every path (review of #60, 4aeb4a4):
        -- the user's row FOR KEY SHARE first, so an account deletion (which locks it FOR UPDATE
        -- and then the user's photos and items) and this deletion wait in one direction only.
        -- If the account is deleted first, the item is gone too and this raises no_data_found.
        PERFORM 1 FROM dolab.users u WHERE u.id = v_user FOR KEY SHARE;
        -- Then the item: a photo insert needs FOR KEY SHARE on it, which FOR UPDATE blocks.
        -- An upload already in progress is waited for (and its photo then deleted and queued);
        -- one that comes after waits, then fails on the foreign key.
        PERFORM 1 FROM dolab.items i WHERE i.id = v_entity AND i.user_id = v_user FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'apply_deletion: no such item for this user' USING ERRCODE = 'no_data_found';
        END IF;
        -- The keys come from the rows this statement deletes, so every deleted photo is queued.
        WITH gone AS (DELETE FROM dolab.item_photos WHERE item_id = v_entity RETURNING original_key, thumb_key)
        SELECT array_agg(DISTINCT k.key::text) INTO v_keys FROM gone
            CROSS JOIN LATERAL (VALUES (gone.original_key), (gone.thumb_key)) AS k(key);
        DELETE FROM dolab.wear_logs WHERE item_id = v_entity;
        DELETE FROM dolab.item_status_changes WHERE item_id = v_entity;
        DELETE FROM dolab.donation_items WHERE item_id = v_entity;
        DELETE FROM dolab.items WHERE id = v_entity;
    WHEN 'photo' THEN
        PERFORM 1 FROM dolab.users u WHERE u.id = v_user FOR KEY SHARE;  -- users first, as above
        IF NOT EXISTS (SELECT 1 FROM dolab.item_photos p WHERE p.id = v_entity AND p.user_id = v_user) THEN
            RAISE EXCEPTION 'apply_deletion: no such photo for this user' USING ERRCODE = 'no_data_found';
        END IF;
        WITH gone AS (DELETE FROM dolab.item_photos WHERE id = v_entity RETURNING original_key, thumb_key)
        SELECT array_agg(DISTINCT k.key::text) INTO v_keys FROM gone
            CROSS JOIN LATERAL (VALUES (gone.original_key), (gone.thumb_key)) AS k(key);
    WHEN 'user' THEN
        -- Lock the account first: every new row of the user (a photo, an item, ...) needs
        -- FOR KEY SHARE on it, which FOR UPDATE blocks. Locks are always taken users before items.
        IF v_entity <> v_user THEN
            RAISE EXCEPTION 'apply_deletion: no such account' USING ERRCODE = 'no_data_found';
        END IF;
        PERFORM 1 FROM dolab.users u WHERE u.id = v_user FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'apply_deletion: no such account' USING ERRCODE = 'no_data_found';
        END IF;
        WITH gone AS (DELETE FROM dolab.item_photos WHERE user_id = v_user RETURNING original_key, thumb_key)
        SELECT array_agg(DISTINCT k.key::text) INTO v_keys FROM gone
            CROSS JOIN LATERAL (VALUES (gone.original_key), (gone.thumb_key)) AS k(key);
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
    -- The photo files, queued in this transaction (notice #59): one statement for all of them.
    IF v_keys IS NOT NULL THEN
        INSERT INTO dolab.pending_file_deletions (key, attempts, next_try_at, created_at)
        SELECT k, 0, now() + interval '5 minutes', now() FROM unnest(v_keys) AS k
        ON CONFLICT DO NOTHING;
        RETURN QUERY SELECT unnest(v_keys);
    END IF;
    -- Step D adds the sync tombstone here, for the server and the reconciliation job alike.
    RETURN;
END
$fn$
"""


def _migration_0005():
    path = Path(__file__).with_name("0005_deleter_functions.py")
    spec = importlib.util.spec_from_file_location("dolab_migration_0005", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


APPLY_DELETION_0005 = _migration_0005().APPLY_DELETION


def upgrade() -> None:
    op.create_table(
        "pending_file_deletions",
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_try_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(40), nullable=True),
        # The queue job's lease: a fresh token for each batch it takes; only the holder of the
        # current token may delete or reschedule the row (review of #60, Medium 4).
        sa.Column("lease_token", sa.String(36), nullable=True),
        schema="dolab",
    )
    op.create_index("ix_pending_file_deletions_next_try_at", "pending_file_deletions", ["next_try_at"],
                    schema="dolab")
    op.execute("GRANT INSERT ON TABLE dolab.pending_file_deletions TO dolab_deleter")
    # apply_deletion locks the item it deletes (SELECT ... FOR UPDATE needs UPDATE on one column),
    # as 0005 did for users.updated_at; the function never updates it.
    op.execute("GRANT UPDATE (updated_at) ON TABLE dolab.items TO dolab_deleter")
    op.execute("GRANT DELETE ON TABLE dolab.pending_file_deletions TO dolab_app")
    op.execute("GRANT SELECT (key) ON TABLE dolab.pending_file_deletions TO dolab_app")
    op.execute("GRANT SELECT, DELETE ON TABLE dolab.pending_file_deletions TO dolab_file_cleaner")
    op.execute("GRANT UPDATE (attempts, next_try_at, last_error, lease_token) ON TABLE dolab.pending_file_deletions "
               "TO dolab_file_cleaner")
    replace_deleter_function(op.execute, APPLY_DELETION, APPLY_SIGNATURE, ["dolab_app"])


class QueueNotEmpty(RuntimeError):
    """The downgrade would drop files still waiting to be deleted from storage."""


def downgrade() -> None:
    # Never drop queued files silently (review of #60): each row is a photo file whose record is
    # gone but which may still be in storage. Refuse, and leave the database exactly as it is.
    # The table is locked ACCESS EXCLUSIVE first, in this migration's own transaction, and stays
    # locked until the DROP below commits: a row being inserted now is waited for and then
    # counted (and the downgrade refused); an insert that comes later waits, and fails once the
    # table is gone, so it never commits a row that the drop would lose (review of #60, High 1).
    op.execute("LOCK TABLE dolab.pending_file_deletions IN ACCESS EXCLUSIVE MODE")
    pending = op.get_bind().execute(sa.text("SELECT count(*) FROM dolab.pending_file_deletions")).scalar()
    if pending:
        raise QueueNotEmpty(
            f"refusing to downgrade 0007: {pending} photo files are still queued in "
            "dolab.pending_file_deletions. Process them first (python -m app.jobs.file_deletions, until "
            "its --dry-run reports nothing due and the queue is empty), then downgrade again.")
    replace_deleter_function(op.execute, APPLY_DELETION_0005, APPLY_SIGNATURE, ["dolab_app"])
    op.execute("REVOKE UPDATE (updated_at) ON TABLE dolab.items FROM dolab_deleter")
    op.drop_index("ix_pending_file_deletions_next_try_at", table_name="pending_file_deletions", schema="dolab")
    op.drop_table("pending_file_deletions", schema="dolab")
