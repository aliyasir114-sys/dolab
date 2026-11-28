"""Every privilege DOLAB expects in PostgreSQL. The tests compare the database against this list
and fail on anything extra (plan 4.10.1د, DB13–DB23). Change it only together with the
migration or bootstrap.sql that changes the database.
"""

SCHEMA = "dolab"
OWNER = "dolab_schema_owner"
DELETER = "dolab_deleter"
DEPLOY = "dolab_deploy"
APP = "dolab_app"
RECONCILER = "dolab_reconciler"
FILE_CLEANER = "dolab_file_cleaner"

DOLAB_ROLES = (OWNER, DELETER, DEPLOY, APP, RECONCILER, FILE_CLEANER)
LOGIN_ROLES = (DEPLOY, APP, RECONCILER, FILE_CLEANER)
OPERATING_ROLES = (APP, RECONCILER, FILE_CLEANER, DELETER)

# Role attributes: (can_login, inherit, connection_limit).
ROLE_ATTRIBUTES = {
    OWNER: (False, False, -1),
    DELETER: (False, False, -1),
    DEPLOY: (True, False, -1),
    APP: (True, False, -1),
    RECONCILER: (True, False, 2),
    FILE_CLEANER: (True, False, 2),
}

# Memberships between DOLAB roles: (granted role, member) -> (inherit, set, admin).
MEMBERSHIPS = {
    (OWNER, DEPLOY): (False, True, False),
    (DELETER, DEPLOY): (False, True, False),
}

# Privileges on the dolab schema (grantee -> privilege letters). PUBLIC gets nothing.
SCHEMA_ACL = {
    OWNER: {"USAGE", "CREATE"},
    APP: {"USAGE"},
    RECONCILER: {"USAGE"},
    FILE_CLEANER: {"USAGE"},
    DELETER: {"USAGE"},
}

# Table privileges, from migrations 0002 to 0005. Tables not listed here fail the inventory test
# until someone decides who may use them. dolab_deleter's DELETE and INSERT come from 0005; its
# SELECT is per column (COLUMNS below), so it has no table-wide SELECT.
_FULL = {"SELECT", "INSERT", "UPDATE", "DELETE"}
_APPEND = {"SELECT", "INSERT", "DELETE"}
_DEL = {"DELETE"}
TABLES = {
    "users": {APP: _FULL, DELETER: _DEL},
    "refresh_tokens": {APP: _FULL, DELETER: _DEL},
    "password_reset_tokens": {APP: _FULL, DELETER: _DEL},
    "profiles": {APP: _FULL, DELETER: _DEL},
    "items": {APP: _FULL, DELETER: _DEL},
    "item_photos": {APP: _APPEND, DELETER: _DEL},
    "item_status_changes": {APP: _APPEND, DELETER: _DEL},
    "wear_logs": {APP: _APPEND, DELETER: _DEL},
    "donations": {APP: _APPEND, DELETER: _DEL},
    "donation_items": {APP: _APPEND, DELETER: _DEL},
    # Migration 0003: counted, recorded, cleared and purged; never edited.
    "rate_limit_events": {APP: _APPEND},
    # Migration 0004: recorded, looked up and removed; never edited.
    "deletion_intents": {APP: _APPEND, DELETER: _DEL},
    # Migration 0007 (notice #59): queued by apply_deletion, removed by the API once it deleted the
    # file (SELECT on key only, in COLUMNS), claimed and rescheduled by the queue job.
    "pending_file_deletions": {APP: _DEL, DELETER: {"INSERT"}, FILE_CLEANER: {"SELECT", "DELETE"}},
    # Migration 0005: written by the deleter functions only; no operating role reads them.
    "reconciled_entries": {DELETER: {"INSERT"}},
    "audit_log": {DELETER: {"INSERT"}},
    # Migration bookkeeping: owner only.
    "alembic_version": {},
}

# Column privileges (migration 0005): what dolab_deleter may read (identifiers, owners, file
# keys), and users.updated_at, which it may lock (SELECT ... FOR UPDATE needs UPDATE on one
# column) but never writes. Step D moves that UPDATE to users.change_seq.
_DELETER_READS = {
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
COLUMNS: dict[tuple[str, str], dict[str, set[str]]] = {
    (table, column): {DELETER: {"SELECT"}} for table, columns in _DELETER_READS.items() for column in columns
}
COLUMNS[("users", "updated_at")] = {DELETER: {"UPDATE"}}
# Migration 0007: the API reads only the key of a queued file, to remove its row.
COLUMNS[("pending_file_deletions", "key")] = {APP: {"SELECT"}}
# apply_deletion locks the item it deletes (migration 0007), as it does the user.
COLUMNS[("items", "updated_at")] = {DELETER: {"UPDATE"}}
# The queue job may change only these (review of #60, M3): never a row's key or created_at.
for _column in ("attempts", "next_try_at", "last_error", "lease_token"):
    COLUMNS[("pending_file_deletions", _column)] = {FILE_CLEANER: {"UPDATE"}}

# Sequences, functions and standalone types in dolab. Functions: the deleter functions of
# migrations 0005 and 0006, owned by dolab_deleter; nobody else may run them.
SEQUENCES: dict[str, dict[str, set[str]]] = {}
FUNCTIONS: dict[str, dict[str, set[str]]] = {
    "dolab.apply_deletion(text,uuid,uuid)": {APP: {"EXECUTE"}},
    "dolab.reconcile_apply(uuid,text,uuid,uuid,boolean)": {RECONCILER: {"EXECUTE"}},
    "dolab.reconcile_record_run(timestamp with time zone,timestamp with time zone,integer,integer,integer,"
    "integer,integer,integer,integer,integer,integer)": {RECONCILER: {"EXECUTE"}},
    # Migration 0006 (notice #57).
    "dolab.reconcile_purge_intents(boolean)": {RECONCILER: {"EXECUTE"}},
}
TYPES: dict[str, dict[str, set[str]]] = {}

# Default privileges (pg_default_acl) per creating role: what PUBLIC loses on new objects.
# Each entry: (creator, object type letter) -> privileges left for grantees other than the creator.
DEFAULT_ACL = {
    (OWNER, "f"): {},  # functions: no EXECUTE for PUBLIC
    (OWNER, "T"): {},  # types: no USAGE for PUBLIC
    (DELETER, "f"): {},
    (DELETER, "T"): {},
}
