"""Sandbox feature flags. Settings are stored here only; they are not connected to any live application."""
import sqlite3

from . import audit, identity
from .db import transaction
from .errors import Conflict, Forbidden, NotFound, ValidationFailed
from .validation import clean_reason

EDIT_PERMISSION = "flags.edit"
DESIRED = {"enable": 1, "disable": 0}


def can_edit(conn: sqlite3.Connection, user) -> bool:
    return identity.has_permission(conn, user, EDIT_PERMISSION)


def list_flags(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT f.*, u.display_name AS updated_by_name FROM feature_flags f"
        " LEFT JOIN users u ON u.id = f.updated_by ORDER BY f.name"
    ).fetchall()


def get_flag(conn: sqlite3.Connection, flag_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT f.*, u.display_name AS updated_by_name FROM feature_flags f"
        " LEFT JOIN users u ON u.id = f.updated_by WHERE f.id = ?",
        (flag_id,),
    ).fetchone()


def history(conn: sqlite3.Connection, flag_id: int) -> list[sqlite3.Row]:
    return audit.history(conn, flag_id=flag_id)


def _stale(flag_id: int, message: str | None = None) -> Conflict:
    return Conflict(
        message or "Someone saved a change to this flag after you opened the form, so your change was not applied.",
        title="This setting has changed",
        back_url=f"/flags/{flag_id}",
        back_label="View the latest version of this flag",
    )


def set_flag(conn: sqlite3.Connection, actor, flag_id: int, desired, reason_raw, expected_version_raw) -> bool:
    """Enable/disable a flag if it is still at expected_version (optimistic concurrency)."""
    if not can_edit(conn, actor):
        raise Forbidden("Only the flag administrator can change sandbox flags.")
    errors = {}
    new_enabled = DESIRED.get(desired)
    if new_enabled is None:
        errors["desired"] = "Choose enable or disable."
    try:
        reason = clean_reason(reason_raw)
    except ValueError as exc:
        errors["reason"] = str(exc)
    try:
        expected_version = int(expected_version_raw)
    except (TypeError, ValueError):
        errors["version"] = "Missing form version. Reload the page."
    if errors:
        raise ValidationFailed("Please correct the errors below.", errors)

    at = audit.now()
    with transaction(conn):
        row = conn.execute("SELECT id, enabled, version FROM feature_flags WHERE id = ?", (flag_id,)).fetchone()
        if row is None:
            raise NotFound("Flag not found.")
        if row["version"] != expected_version:
            raise _stale(flag_id)
        if row["enabled"] == new_enabled:
            raise _stale(flag_id, f"This flag is already {'enabled' if new_enabled else 'disabled'}.")
        cur = conn.execute(
            "UPDATE feature_flags SET enabled = ?, version = version + 1, updated_at = ?, updated_by = ?"
            " WHERE id = ? AND version = ?",
            (new_enabled, at, actor["id"], flag_id, expected_version),
        )
        if cur.rowcount != 1:
            raise _stale(flag_id)
        old, new = ("enabled" if row["enabled"] else "disabled"), ("enabled" if new_enabled else "disabled")
        audit.record(conn, flag_id=flag_id, actor_id=actor["id"], action=f"flag_{new}",
                     from_status=old, to_status=new, reason=reason, at=at)
    return bool(new_enabled)
