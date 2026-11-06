"""Shared audit trail. The application only inserts audit rows; it never updates or deletes them."""
import sqlite3
from datetime import datetime, timezone


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(conn: sqlite3.Connection, *, actor_id: int, action: str, from_status, to_status: str,
           reason: str, at: str, request_id: int | None = None, flag_id: int | None = None) -> None:
    """Insert one audit event. Call inside the same transaction as the change it describes."""
    conn.execute(
        "INSERT INTO audit_events"
        " (request_id, flag_id, actor_id, action, from_status, to_status, reason, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (request_id, flag_id, actor_id, action, from_status, to_status, reason, at),
    )


def history(conn: sqlite3.Connection, *, request_id: int | None = None,
            flag_id: int | None = None) -> list[sqlite3.Row]:
    column, value = ("request_id", request_id) if request_id is not None else ("flag_id", flag_id)
    return conn.execute(
        "SELECT a.*, u.display_name AS actor_name, u.role AS actor_role"
        " FROM audit_events a JOIN users u ON u.id = a.actor_id"
        f" WHERE a.{column} = ? ORDER BY a.id",
        (value,),
    ).fetchall()
