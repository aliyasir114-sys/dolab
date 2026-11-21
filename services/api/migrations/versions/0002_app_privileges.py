"""Privileges of the API role dolab_app on the baseline tables (condition of decision 20).

Revision ID: 0002_app_privileges
Revises: 0001_baseline
Create Date: 2026-11-20

The API gets only what the code in app/routers uses, table by table. Never TRUNCATE,
REFERENCES or TRIGGER; nothing on alembic_version; no sequences (identifiers are UUID4).

    users, refresh_tokens, password_reset_tokens, profiles, items
        SELECT, INSERT, UPDATE, DELETE
        (sign-in state, settings and items change in place; DELETE for account deletion.
         UPDATE on items also allows SELECT ... FOR UPDATE, used to lock an item.)
    item_photos, item_status_changes, wear_logs, donations, donation_items
        SELECT, INSERT, DELETE
        (written once and never edited; DELETE only when an item or the account is deleted)

The same list is the test oracle in dbtools/expected_acl.py; a change here must change both.
This migration keeps its own copy on purpose: an applied migration must not change later.
"""
from alembic import op

revision = "0002_app_privileges"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

APP_ROLE = "dolab_app"
MATRIX = {
    "users": "SELECT, INSERT, UPDATE, DELETE",
    "refresh_tokens": "SELECT, INSERT, UPDATE, DELETE",
    "password_reset_tokens": "SELECT, INSERT, UPDATE, DELETE",
    "profiles": "SELECT, INSERT, UPDATE, DELETE",
    "items": "SELECT, INSERT, UPDATE, DELETE",
    "item_photos": "SELECT, INSERT, DELETE",
    "item_status_changes": "SELECT, INSERT, DELETE",
    "wear_logs": "SELECT, INSERT, DELETE",
    "donations": "SELECT, INSERT, DELETE",
    "donation_items": "SELECT, INSERT, DELETE",
}


def upgrade() -> None:
    for table, privileges in MATRIX.items():
        op.execute(f"GRANT {privileges} ON TABLE dolab.{table} TO {APP_ROLE}")


def downgrade() -> None:
    for table in MATRIX:
        op.execute(f"REVOKE ALL ON TABLE dolab.{table} FROM {APP_ROLE}")
