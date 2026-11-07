import sqlite3

import pytest

from app.db import init_db
from app.refunds import parse_amount_cents


@pytest.mark.parametrize("raw,cents", [("12.50", 1250), ("12.5", 1250), ("0.01", 1), ("7", 700),
                                       (" 19.99 ", 1999), ("0.29", 29), ("1000000", 100_000_000),
                                       ("1000000.00", 100_000_000), ("4.35", 435)])
def test_dollars_to_cents_exact(raw, cents):
    assert parse_amount_cents(raw) == cents


@pytest.mark.parametrize("raw", ["12.505", "0", "0.00", "-1", "12.", ".5", "1e2", "inf", "1,000", "$1",
                                 "1000000.01", "", None, "1 000", "١٢"])
def test_invalid_dollars_rejected(raw):
    with pytest.raises(ValueError):
        parse_amount_cents(raw)


OLD_SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('operator','reviewer','viewer')));
CREATE TABLE refund_requests (id INTEGER PRIMARY KEY, amount_cents INTEGER NOT NULL, reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', submitted_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL, decided_by INTEGER, decision_reason TEXT, decided_at TEXT);
CREATE TABLE audit_events (id INTEGER PRIMARY KEY, request_id INTEGER NOT NULL REFERENCES refund_requests(id),
    actor_id INTEGER NOT NULL REFERENCES users(id), action TEXT NOT NULL, from_status TEXT,
    to_status TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
INSERT INTO users VALUES (1, 'olivia', 'Olivia Operator', 'operator');
INSERT INTO refund_requests (id, amount_cents, reason, submitted_by, created_at) VALUES (1, 500, 'r', 1, 't');
INSERT INTO audit_events VALUES (7, 1, 1, 'created', NULL, 'pending', 'r', 't');
"""


def test_existing_refund_audit_history_preserved_by_migration(tmp_path):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript(OLD_SCHEMA)
    old.close()
    init_db(path)
    init_db(path)  # idempotent
    conn = sqlite3.connect(path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(audit_events)")]
    assert "flag_id" in cols
    assert conn.execute("SELECT id, request_id, flag_id, action, reason FROM audit_events").fetchall() == \
        [(7, 1, None, "created", "r")]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
