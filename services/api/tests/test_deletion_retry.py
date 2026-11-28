"""A deletion that PostgreSQL rolls back to break a deadlock is tried once more, and only once
(review of #60, High 2). Runs on SQLite and PostgreSQL: the error is raised by a stand-in."""
import pytest

from app import deletion
from tests.test_photos import new_item, upload


class Deadlock(Exception):
    sqlstate = "40P01"  # what psycopg reports for deadlock_detected


class OtherError(Exception):
    sqlstate = "23503"  # foreign_key_violation: not retried


def failing_first(monkeypatch, errors):
    real, calls = deletion.apply_deletion, []

    def apply(db, *args):
        calls.append(args)
        if errors:
            raise errors.pop(0)
        return real(db, *args)

    monkeypatch.setattr(deletion, "apply_deletion", apply)
    return calls


def test_a_deadlock_is_retried_once_and_the_deletion_completes(client, signup, monkeypatch, caplog):
    headers = signup()
    item_id = new_item(client, headers)
    upload(client, headers, item_id)
    calls = failing_first(monkeypatch, [Deadlock("deadlock detected")])
    assert client.delete(f"/items/{item_id}", headers=headers).status_code == 204
    assert len(calls) == 2 and client.get(f"/items/{item_id}", headers=headers).status_code == 404
    assert "deletion retried after PostgreSQL error 40P01 (try 1 of 2)" in caplog.text


def test_a_second_deadlock_is_raised_not_hidden(client, signup, monkeypatch):
    headers = signup()
    item_id = new_item(client, headers)
    calls = failing_first(monkeypatch, [Deadlock("one"), Deadlock("two")])
    with pytest.raises(Deadlock):
        client.delete(f"/items/{item_id}", headers=headers)
    monkeypatch.undo()
    assert len(calls) == 2
    assert client.get(f"/items/{item_id}", headers=headers).status_code == 200  # nothing was deleted


def test_other_errors_are_not_retried(client, signup, monkeypatch):
    headers = signup()
    item_id = new_item(client, headers)
    calls = failing_first(monkeypatch, [OtherError("foreign key")])
    with pytest.raises(OtherError):
        client.delete(f"/items/{item_id}", headers=headers)
    monkeypatch.undo()
    assert len(calls) == 1
    assert client.get(f"/items/{item_id}", headers=headers).status_code == 200
