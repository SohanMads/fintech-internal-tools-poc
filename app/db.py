import sqlite3
from contextlib import contextmanager

DEFAULT_BUSY_TIMEOUT_MS = 2000

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('operator', 'reviewer', 'viewer'))
);

CREATE TABLE IF NOT EXISTS refund_requests (
    id INTEGER PRIMARY KEY,
    amount_cents INTEGER NOT NULL
        CHECK (typeof(amount_cents) = 'integer' AND amount_cents > 0),
    reason TEXT NOT NULL
        CHECK (length(trim(reason)) > 0 AND reason = trim(reason)),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    submitted_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    decided_by INTEGER REFERENCES users(id),
    decision_reason TEXT
        CHECK (decision_reason IS NULL
               OR (length(trim(decision_reason)) > 0 AND decision_reason = trim(decision_reason))),
    decided_at TEXT,
    CHECK (
        (status = 'pending'
            AND decided_by IS NULL AND decision_reason IS NULL AND decided_at IS NULL)
        OR (status IN ('approved', 'rejected')
            AND decided_by IS NOT NULL AND decision_reason IS NOT NULL AND decided_at IS NOT NULL)
    ),
    CHECK (decided_by IS NULL OR decided_by <> submitted_by)
);

CREATE INDEX IF NOT EXISTS ix_refund_requests_status ON refund_requests(status);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY,
    request_id INTEGER NOT NULL REFERENCES refund_requests(id),
    actor_id INTEGER NOT NULL REFERENCES users(id),
    action TEXT NOT NULL CHECK (action IN ('created', 'approved', 'rejected')),
    from_status TEXT CHECK (from_status IS NULL OR from_status IN ('pending', 'approved', 'rejected')),
    to_status TEXT NOT NULL CHECK (to_status IN ('pending', 'approved', 'rejected')),
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_audit_events_request ON audit_events(request_id);
"""

DEMO_USERS = [
    ("olivia", "Olivia Operator", "operator"),
    ("rita", "Rita Reviewer", "reviewer"),
    ("raj", "Raj Reviewer", "reviewer"),
    ("victor", "Victor Viewer", "viewer"),
]


class DatabaseBusy(Exception):
    pass


def connect(path: str, busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS) -> sqlite3.Connection:
    conn = sqlite3.connect(
        path,
        timeout=busy_timeout_ms / 1000,
        isolation_level=None,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    return conn


def _is_lock_error(exc: sqlite3.OperationalError) -> bool:
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


@contextmanager
def transaction(conn: sqlite3.Connection):
    """Write transaction that takes the write lock up front (BEGIN IMMEDIATE)."""
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        if _is_lock_error(exc):
            raise DatabaseBusy() from exc
        raise
    try:
        yield conn
        conn.execute("COMMIT")
    except sqlite3.OperationalError as exc:
        conn.execute("ROLLBACK")
        if _is_lock_error(exc):
            raise DatabaseBusy() from exc
        raise
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def init_db(path: str) -> None:
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        with transaction(conn):
            conn.executemany(
                "INSERT OR IGNORE INTO users (username, display_name, role) VALUES (?, ?, ?)",
                DEMO_USERS,
            )
    finally:
        conn.close()
