"""Alembic environment (plan 4.4 and 4.10.1د).

Connects as dolab_deploy, then switches to dolab_schema_owner *before* Alembic creates or reads
its version table, so every table, index and the version table itself belong to the schema
owner and dolab_deploy never needs privileges of its own.
"""
import os

from alembic import context
from sqlalchemy import create_engine, pool

from app import models  # noqa: F401  (registers the tables)
from app.base import SCHEMA, Base

OWNER_ROLE = "dolab_schema_owner"
VERSION_TABLE = "alembic_version"


def _url() -> str:
    url = os.getenv("DOLAB_MIGRATION_URL", "")
    if not url.startswith("postgresql"):
        raise RuntimeError(
            "DOLAB_MIGRATION_URL must be a PostgreSQL URL for the dolab_deploy identity. "
            "SQLite is not migrated: its tables are created by the server for local development."
        )
    return url


def include_name(name, type_, parent_names) -> bool:
    # Compare only the dolab schema. The default schema (reported as None) is pg_catalog here.
    if type_ == "schema":
        return name == SCHEMA
    if type_ == "table":
        return name != VERSION_TABLE
    return True


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        connection.exec_driver_sql(f"SET ROLE {OWNER_ROLE}")
        # dolab must not be the default schema, or Alembic would compare it under the name None.
        connection.exec_driver_sql("SET search_path = pg_catalog")
        connection.exec_driver_sql("SET lock_timeout = '10s'")
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            version_table=VERSION_TABLE,
            version_table_schema=SCHEMA,
            include_schemas=True,
            include_name=include_name,
            compare_type=True,
            # One transaction for the whole upgrade: a failure leaves the database as it was.
            transaction_per_migration=False,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Offline (SQL script) migrations are not supported; run against the database.")
run_migrations_online()
