"""Create or replace a function owned by dolab_deleter, from inside a migration (plan 4.10.1د).

Owning the schema does not let dolab_schema_owner replace a function owned by another role, so
the function is written as dolab_deleter itself. dolab_deleter has no CREATE on the schema while
the system runs; it gets it for the length of the migration's transaction only:

    as dolab_schema_owner: GRANT CREATE ON SCHEMA dolab TO dolab_deleter
    as dolab_deleter:      CREATE OR REPLACE FUNCTION ...; REVOKE ALL ... FROM PUBLIC; GRANT EXECUTE ...
    as dolab_schema_owner: REVOKE CREATE ON SCHEMA dolab FROM dolab_deleter

GRANT, REVOKE and SET ROLE are all undone if the transaction fails, so a failed migration leaves
the old function and the old privileges exactly as they were. Alembic runs the whole upgrade in
one transaction (migrations/env.py).

Use from a migration:  replace_deleter_function(op.execute, sql, "dolab.f(uuid)", ["dolab_reconciler"])
"""
from collections.abc import Callable, Iterable

OWNER = "dolab_schema_owner"
DELETER = "dolab_deleter"


def replace_deleter_function(
    execute: Callable[[str], object],
    create_sql: str,
    signature: str,
    execute_roles: Iterable[str] = (),
) -> None:
    if not signature.startswith("dolab."):
        raise ValueError("name the function with its schema: dolab.<name>(<argument types>)")
    if "SECURITY DEFINER" not in create_sql.upper() or "SEARCH_PATH" not in create_sql.upper():
        raise ValueError("deleter functions must be SECURITY DEFINER with a fixed search_path")
    execute(f"SET ROLE {OWNER}")
    execute(f"GRANT CREATE ON SCHEMA dolab TO {DELETER}")
    execute(f"SET ROLE {DELETER}")
    execute(create_sql)
    execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    for role in execute_roles:
        execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {role}")
    execute(f"SET ROLE {OWNER}")
    execute(f"REVOKE CREATE ON SCHEMA dolab FROM {DELETER}")
