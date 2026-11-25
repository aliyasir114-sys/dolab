"""Deletion intents (plan 4.10.1د, step C, deletion ledger part 2).

Revision ID: 0004_deletion_intents
Revises: 0003_rate_limit_events
Create Date: 2026-11-25

The server records an intent, committed on its own, before it writes a deletion's ledger entry,
and removes it in the same transaction as the deletion. dolab_app may SELECT, INSERT and DELETE
rows (never UPDATE); dolab_reconciler gets nothing here: it will reach intents only through the
deleter functions of the next part. The same entry is in dbtools/expected_acl.py.
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_deletion_intents"
down_revision = "0003_rate_limit_events"
branch_labels = None
depends_on = None

S = "dolab"


def upgrade() -> None:
    op.create_table(
        "deletion_intents",
        sa.Column("entry_id", sa.String(36), nullable=False),
        sa.Column("entity_type", sa.String(10), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("entry_id", name="pk_deletion_intents"),
        schema=S,
    )
    op.create_index("ix_deletion_intents_created_at", "deletion_intents", ["created_at"], schema=S)
    op.execute("GRANT SELECT, INSERT, DELETE ON TABLE dolab.deletion_intents TO dolab_app")


def downgrade() -> None:
    op.execute("REVOKE ALL ON TABLE dolab.deletion_intents FROM dolab_app")
    op.drop_table("deletion_intents", schema=S)
