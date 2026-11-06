"""Demo identities. Roles and grants always come from the server-side tables."""
import sqlite3


def get_user(conn: sqlite3.Connection, user_id) -> sqlite3.Row | None:
    if user_id is None:
        return None
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def list_users(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT u.*, group_concat(p.permission, ', ') AS grants"
        " FROM users u LEFT JOIN tool_permissions p ON p.user_id = u.id"
        " GROUP BY u.id ORDER BY u.id"
    ).fetchall()


def has_permission(conn: sqlite3.Connection, user, permission: str) -> bool:
    if user is None:
        return False
    return conn.execute(
        "SELECT 1 FROM tool_permissions WHERE user_id = ? AND permission = ?",
        (user["id"], permission),
    ).fetchone() is not None
