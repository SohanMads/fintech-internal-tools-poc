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

CREATE TABLE IF NOT EXISTS tool_permissions (
    user_id INTEGER NOT NULL REFERENCES users(id),
    permission TEXT NOT NULL CHECK (permission IN ('flags.edit')),
    PRIMARY KEY (user_id, permission)
);

CREATE TABLE IF NOT EXISTS feature_flags (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    updated_at TEXT,
    updated_by INTEGER REFERENCES users(id)
);
"""

# Audit events for both tools: exactly one of request_id / flag_id is set.
AUDIT_TABLE = """
CREATE TABLE {name} (
    id INTEGER PRIMARY KEY,
    request_id INTEGER REFERENCES refund_requests(id),
    flag_id INTEGER REFERENCES feature_flags(id),
    actor_id INTEGER NOT NULL REFERENCES users(id),
    action TEXT NOT NULL
        CHECK (action IN ('created', 'approved', 'rejected', 'flag_enabled', 'flag_disabled')),
    from_status TEXT
        CHECK (from_status IS NULL OR from_status IN ('pending', 'approved', 'rejected', 'enabled', 'disabled')),
    to_status TEXT NOT NULL
        CHECK (to_status IN ('pending', 'approved', 'rejected', 'enabled', 'disabled')),
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    created_at TEXT NOT NULL,
    CHECK (
        (request_id IS NOT NULL AND flag_id IS NULL AND action IN ('created', 'approved', 'rejected'))
        OR (flag_id IS NOT NULL AND request_id IS NULL AND action IN ('flag_enabled', 'flag_disabled'))
    )
)
"""

AUDIT_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_audit_events_request ON audit_events(request_id);
CREATE INDEX IF NOT EXISTS ix_audit_events_flag ON audit_events(flag_id);
"""

DEMO_USERS = [
    ("olivia", "Olivia Operator", "operator"),
    ("rita", "Rita Reviewer", "reviewer"),
    ("raj", "Raj Reviewer", "reviewer"),
    ("victor", "Victor Viewer", "viewer"),
    ("fiona", "Fiona Flag Admin", "viewer"),
]

DEMO_GRANTS = [("fiona", "flags.edit")]

DEMO_FLAGS = [
    ("new-queue-layout", "New queue layout", "Show the redesigned request queue.", 0),
    ("csv-export", "CSV export", "Allow exporting the queue as CSV.", 1),
    ("reviewer-reminders", "Reviewer reminders", "Remind reviewers about stale pending requests.", 0),
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


def _columns(conn, table) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate_audit_events(conn: sqlite3.Connection) -> None:
    """Rebuild a v1 audit_events table (request-only) into the shared shape, keeping every row.

    SQLite cannot relax NOT NULL in place, so this follows the documented
    create-copy-drop-rename procedure inside one transaction.
    """
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        with transaction(conn):
            before = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
            conn.execute(AUDIT_TABLE.format(name="audit_events_new"))
            conn.execute(
                "INSERT INTO audit_events_new"
                " (id, request_id, flag_id, actor_id, action, from_status, to_status, reason, created_at)"
                " SELECT id, request_id, NULL, actor_id, action, from_status, to_status, reason, created_at"
                " FROM audit_events"
            )
            conn.execute("DROP TABLE audit_events")
            conn.execute("ALTER TABLE audit_events_new RENAME TO audit_events")
            after = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
            if after != before or conn.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("audit_events migration verification failed")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def init_db(path: str) -> None:
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        existing = _columns(conn, "audit_events")
        if not existing:
            conn.execute(AUDIT_TABLE.format(name="audit_events"))
        elif "flag_id" not in existing:
            _migrate_audit_events(conn)
        conn.executescript(AUDIT_INDEXES)
        with transaction(conn):
            conn.executemany(
                "INSERT OR IGNORE INTO users (username, display_name, role) VALUES (?, ?, ?)",
                DEMO_USERS,
            )
            conn.executemany(
                "INSERT OR IGNORE INTO tool_permissions (user_id, permission)"
                " SELECT id, ? FROM users WHERE username = ?",
                [(perm, username) for username, perm in DEMO_GRANTS],
            )
            conn.executemany(
                "INSERT OR IGNORE INTO feature_flags (key, name, description, enabled) VALUES (?, ?, ?, ?)",
                DEMO_FLAGS,
            )
    finally:
        conn.close()
