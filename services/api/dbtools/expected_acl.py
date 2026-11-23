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

DOLAB_ROLES = (OWNER, DELETER, DEPLOY, APP, RECONCILER)
LOGIN_ROLES = (DEPLOY, APP, RECONCILER)
OPERATING_ROLES = (APP, RECONCILER, DELETER)

# Role attributes: (can_login, inherit, connection_limit).
ROLE_ATTRIBUTES = {
    OWNER: (False, False, -1),
    DELETER: (False, False, -1),
    DEPLOY: (True, False, -1),
    APP: (True, False, -1),
    RECONCILER: (True, False, 2),
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
    DELETER: {"USAGE"},
}

# Table privileges, from migrations 0002_app_privileges and 0003_rate_limit_events. Tables not listed here fail the
# inventory test until someone decides who may use them.
_FULL = {"SELECT", "INSERT", "UPDATE", "DELETE"}
_APPEND = {"SELECT", "INSERT", "DELETE"}
TABLES = {
    "users": {APP: _FULL},
    "refresh_tokens": {APP: _FULL},
    "password_reset_tokens": {APP: _FULL},
    "profiles": {APP: _FULL},
    "items": {APP: _FULL},
    "item_photos": {APP: _APPEND},
    "item_status_changes": {APP: _APPEND},
    "wear_logs": {APP: _APPEND},
    "donations": {APP: _APPEND},
    "donation_items": {APP: _APPEND},
    # Migration 0003: counted, recorded, cleared and purged; never edited.
    "rate_limit_events": {APP: _APPEND},
    # Migration bookkeeping: owner only.
    "alembic_version": {},
}

# Sequences, functions and standalone types in dolab. None exist yet.
SEQUENCES: dict[str, dict[str, set[str]]] = {}
FUNCTIONS: dict[str, dict[str, set[str]]] = {}
TYPES: dict[str, dict[str, set[str]]] = {}

# Default privileges (pg_default_acl) per creating role: what PUBLIC loses on new objects.
# Each entry: (creator, object type letter) -> privileges left for grantees other than the creator.
DEFAULT_ACL = {
    (OWNER, "f"): {},  # functions: no EXECUTE for PUBLIC
    (OWNER, "T"): {},  # types: no USAGE for PUBLIC
    (DELETER, "f"): {},
    (DELETER, "T"): {},
}
