"""Baseline: the tables as they are in main after step A.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-20

Written by hand to match app/models.py exactly; CI runs `alembic check` to prove it.
No PostgreSQL database has been deployed before this migration, so there is nothing to stamp.
A PostgreSQL made earlier by the server's create_all has its tables in "public". This migration
refuses to run there instead of creating a second, empty set of tables beside the old data:
such a database must be recreated (or its data copied by hand) before migrating.

Downgrade drops every table and all data in them. It exists for tests and for an empty
database; it is never run on production.
"""
import sqlalchemy as sa
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

S = "dolab"
ID = sa.String(36)


def _ts(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{S}.{target}.id"], name=f"fk_{table}_{column}_{target}"
    )


def _index(table: str, column: str, unique: bool = False) -> None:
    op.create_index(f"ix_{table}_{column}", table, [column], unique=unique, schema=S)


TABLES = (
    "users",
    "refresh_tokens",
    "password_reset_tokens",
    "profiles",
    "items",
    "item_photos",
    "item_status_changes",
    "wear_logs",
    "donations",
    "donation_items",
)


def _refuse_pre_migration_tables() -> None:
    # Read the catalog, not the tables: the migration identity has no rights on "public".
    found = op.get_bind().execute(
        sa.text(
            "SELECT string_agg(n.nspname || '.' || c.relname, ', ' ORDER BY c.relname) "
            "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind IN ('r', 'p') AND c.relname = ANY(:names) "
            "AND n.nspname NOT IN ('dolab', 'pg_catalog', 'information_schema')"
        ),
        {"names": list(TABLES)},
    ).scalar()
    if found:
        raise RuntimeError(
            f"DOLAB tables already exist outside the dolab schema ({found}), probably made by an "
            "older server with create_all. The baseline would not move their data. Recreate the "
            "database, or copy the data into dolab by hand, then migrate."
        )


def upgrade() -> None:
    _refuse_pre_migration_tables()
    op.create_table(
        "users",
        sa.Column("id", ID, nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("locale", sa.String(5), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("account_type", sa.String(20), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        schema=S,
    )
    _index("users", "email", unique=True)

    op.create_table(
        "refresh_tokens",
        sa.Column("id", ID, nullable=False),
        sa.Column("user_id", ID, nullable=False),
        sa.Column("family_id", ID, nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        _ts("created_at"),
        _ts("expires_at"),
        _ts("revoked_at", nullable=True),
        _fk("refresh_tokens", "user_id", "users"),
        sa.PrimaryKeyConstraint("id", name="pk_refresh_tokens"),
        schema=S,
    )
    _index("refresh_tokens", "user_id")
    _index("refresh_tokens", "family_id")
    _index("refresh_tokens", "token_hash", unique=True)

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", ID, nullable=False),
        sa.Column("user_id", ID, nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        _ts("created_at"),
        _ts("expires_at"),
        _ts("used_at", nullable=True),
        _fk("password_reset_tokens", "user_id", "users"),
        sa.PrimaryKeyConstraint("id", name="pk_password_reset_tokens"),
        schema=S,
    )
    _index("password_reset_tokens", "user_id")
    _index("password_reset_tokens", "token_hash", unique=True)

    op.create_table(
        "profiles",
        sa.Column("user_id", ID, nullable=False),
        sa.Column("height_cm", sa.Float(), nullable=True),
        sa.Column("weight_kg", sa.Float(), nullable=True),
        sa.Column("usual_size", sa.String(10), nullable=True),
        sa.Column("body_shape", sa.String(30), nullable=True),
        sa.Column("face_shape", sa.String(30), nullable=True),
        _ts("updated_at"),
        _fk("profiles", "user_id", "users"),
        sa.PrimaryKeyConstraint("user_id", name="pk_profiles"),
        schema=S,
    )

    op.create_table(
        "items",
        sa.Column("id", ID, nullable=False),
        sa.Column("user_id", ID, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("category", sa.String(60), nullable=False),
        sa.Column("primary_color", sa.String(40), nullable=True),
        sa.Column("secondary_colors", sa.JSON(), nullable=False),
        sa.Column("color_confidence", sa.Float(), nullable=True),
        sa.Column("color_confirmed", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("purchase_date", sa.Date(), nullable=True),
        sa.Column("purchase_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        _ts("added_at"),
        _ts("updated_at"),
        sa.Column("wear_count", sa.Integer(), nullable=False),
        sa.Column("last_worn_at", sa.Date(), nullable=True),
        _fk("items", "user_id", "users"),
        sa.PrimaryKeyConstraint("id", name="pk_items"),
        schema=S,
    )
    _index("items", "user_id")
    _index("items", "status")

    op.create_table(
        "item_photos",
        sa.Column("id", ID, nullable=False),
        sa.Column("item_id", ID, nullable=False),
        sa.Column("user_id", ID, nullable=False),
        sa.Column("original_key", sa.String(200), nullable=False),
        sa.Column("thumb_key", sa.String(200), nullable=False),
        sa.Column("content_type", sa.String(30), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        _ts("created_at"),
        _fk("item_photos", "item_id", "items"),
        _fk("item_photos", "user_id", "users"),
        sa.PrimaryKeyConstraint("id", name="pk_item_photos"),
        schema=S,
    )
    _index("item_photos", "item_id")
    _index("item_photos", "user_id")

    op.create_table(
        "item_status_changes",
        sa.Column("id", ID, nullable=False),
        sa.Column("item_id", ID, nullable=False),
        sa.Column("user_id", ID, nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(20), nullable=False),
        sa.Column("ref_id", ID, nullable=True),
        _ts("changed_at"),
        _fk("item_status_changes", "item_id", "items"),
        _fk("item_status_changes", "user_id", "users"),
        sa.PrimaryKeyConstraint("id", name="pk_item_status_changes"),
        schema=S,
    )
    _index("item_status_changes", "item_id")
    _index("item_status_changes", "user_id")

    op.create_table(
        "wear_logs",
        sa.Column("id", ID, nullable=False),
        sa.Column("user_id", ID, nullable=False),
        sa.Column("item_id", ID, nullable=False),
        sa.Column("worn_on", sa.Date(), nullable=False),
        _fk("wear_logs", "user_id", "users"),
        _fk("wear_logs", "item_id", "items"),
        sa.PrimaryKeyConstraint("id", name="pk_wear_logs"),
        sa.UniqueConstraint("item_id", "worn_on", name="uq_wear_item_day"),
        schema=S,
    )
    _index("wear_logs", "user_id")
    _index("wear_logs", "item_id")

    op.create_table(
        "donations",
        sa.Column("id", ID, nullable=False),
        sa.Column("user_id", ID, nullable=False),
        sa.Column("recipient_type", sa.String(10), nullable=False),
        sa.Column("recipient_name", sa.String(120), nullable=True),
        _ts("confirmed_at"),
        _fk("donations", "user_id", "users"),
        sa.PrimaryKeyConstraint("id", name="pk_donations"),
        schema=S,
    )
    _index("donations", "user_id")

    op.create_table(
        "donation_items",
        sa.Column("donation_id", ID, nullable=False),
        sa.Column("item_id", ID, nullable=False),
        _fk("donation_items", "donation_id", "donations"),
        _fk("donation_items", "item_id", "items"),
        sa.PrimaryKeyConstraint("donation_id", "item_id", name="pk_donation_items"),
        schema=S,
    )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table, schema=S)
