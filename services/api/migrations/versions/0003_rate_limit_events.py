"""Shared rate limits: the table rate_limit_events (plan 4.7, step C).

Revision ID: 0003_rate_limit_events
Revises: 0002_app_privileges
Create Date: 2026-11-23

Sign-in and reset-email limits move from each process's memory to this table, so they hold
across server instances and restarts. Keys are HMACs; no email or address is stored.

dolab_app gets SELECT, INSERT, DELETE (count, record, clear and purge; rows are never edited).
The same entry is in dbtools/expected_acl.py.
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_rate_limit_events"
down_revision = "0002_app_privileges"
branch_labels = None
depends_on = None

S = "dolab"


def upgrade() -> None:
    op.create_table(
        "rate_limit_events",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("subject_hash", sa.String(64), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_rate_limit_events"),
        schema=S,
    )
    op.create_index("ix_rate_limit_events_scope", "rate_limit_events", ["scope", "key_hash", "at"], schema=S)
    op.create_index("ix_rate_limit_events_subject_hash", "rate_limit_events", ["subject_hash"], schema=S)
    op.create_index("ix_rate_limit_events_at", "rate_limit_events", ["at"], schema=S)
    op.execute("GRANT SELECT, INSERT, DELETE ON TABLE dolab.rate_limit_events TO dolab_app")


def downgrade() -> None:
    op.execute("REVOKE ALL ON TABLE dolab.rate_limit_events FROM dolab_app")
    op.drop_table("rate_limit_events", schema=S)
