"""Declarative base and the schema name. No settings here, so migrations can import the models
without the server's configuration."""
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Every table lives in the PostgreSQL schema "dolab" and is always named with it (dolab.items),
# so no query depends on search_path (plan 4.10.1د). SQLite has no schemas: there the name is
# dropped at execution time (see db.py).
SCHEMA = "dolab"

# Fixed constraint and index names, so the migrations and the models always agree.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA, naming_convention=NAMING_CONVENTION)
