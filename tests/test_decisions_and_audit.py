import sqlite3
import threading

import pytest

from app import audit
from app import refunds as services
from app.db import DatabaseBusy, connect

from app.main import create_app

from .conftest import UserClient, created_id


def audit_rows(conn, rid):
    return conn.execute("SELECT * FROM audit_events WHERE request_id=? ORDER BY id", (rid,)).fetchall()


def test_second_decision_conflicts(as_user, conn, users):
    rid = created_id(as_user("olivia").submit())
    assert as_user("rita").decide(rid, "approve", "ok").status_code == 303
    assert as_user("raj").decide(rid, "reject", "no").status_code == 409
    row = conn.execute("SELECT * FROM refund_requests WHERE id=?", (rid,)).fetchone()
    assert (row["status"], row["decided_by"], row["decision_reason"]) == ("approved", users["rita"]["id"], "ok")
    assert [a["action"] for a in audit_rows(conn, rid)] == ["created", "approved"]


def test_concurrent_decisions_one_winner(db_path, conn, users):
    rid = services.create_request(conn, users["olivia"], "5000", "Synthetic")
    barrier = threading.Barrier(2)
    results = {}

    def attempt(name, decision):
        c = connect(db_path)
        try:
            barrier.wait()
            results[name] = services.decide(c, users[name], rid, decision, f"{name} decided")
        except services.Conflict as exc:
            results[name] = exc
        finally:
            c.close()

    threads = [threading.Thread(target=attempt, args=a) for a in (("rita", "approve"), ("raj", "reject"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = {k: v for k, v in results.items() if isinstance(v, str)}
    losers = {k: v for k, v in results.items() if isinstance(v, services.Conflict)}
    assert len(winners) == 1 and len(losers) == 1
    (winner, status), = winners.items()
    row = conn.execute("SELECT * FROM refund_requests WHERE id=?", (rid,)).fetchone()
    assert row["status"] == status and row["decided_by"] == users[winner]["id"]
    decisions = [a for a in audit_rows(conn, rid) if a["action"] != "created"]
    assert len(decisions) == 1 and decisions[0]["actor_id"] == users[winner]["id"]


def test_audit_matches_every_change(as_user, conn, users):
    olivia, rita, raj = as_user("olivia"), as_user("rita"), as_user("raj")
    a = created_id(olivia.submit(amount="100", reason="a"))
    b = created_id(olivia.submit(amount="200", reason="b"))
    c = created_id(rita.submit(amount="300", reason="c"))
    raj.decide(a, "approve", "fine")
    rita.decide(b, "reject", "dup")
    raj.decide(c, "approve", "ok")
    rita.decide(a, "reject", "late")  # conflict, must not write audit

    for rid in (a, b, c):
        req = conn.execute("SELECT * FROM refund_requests WHERE id=?", (rid,)).fetchone()
        events = audit_rows(conn, rid)
        assert [e["action"] for e in events] == ["created", req["status"]]
        created, decided = events
        assert (created["actor_id"], created["to_status"], created["reason"]) == \
            (req["submitted_by"], "pending", req["reason"])
        assert (decided["actor_id"], decided["from_status"], decided["to_status"], decided["reason"],
                decided["created_at"]) == \
            (req["decided_by"], "pending", req["status"], req["decision_reason"], req["decided_at"])

    decided_count = conn.execute("SELECT COUNT(*) FROM refund_requests WHERE status<>'pending'").fetchone()[0]
    decision_events = conn.execute("SELECT COUNT(*) FROM audit_events WHERE action<>'created'").fetchone()[0]
    assert decided_count == decision_events == 3


def test_audit_failure_rolls_back_create(conn, users, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("audit write failed")
    monkeypatch.setattr(audit, "record", boom)
    with pytest.raises(RuntimeError):
        services.create_request(conn, users["olivia"], "100", "x")
    assert conn.execute("SELECT COUNT(*) FROM refund_requests").fetchone()[0] == 0


def test_audit_failure_rolls_back_decision(conn, users, monkeypatch):
    rid = services.create_request(conn, users["olivia"], "100", "x")

    def boom(*a, **k):
        raise RuntimeError("audit write failed")
    monkeypatch.setattr(audit, "record", boom)
    with pytest.raises(RuntimeError):
        services.decide(conn, users["raj"], rid, "approve", "ok")
    row = conn.execute("SELECT status, decided_by FROM refund_requests WHERE id=?", (rid,)).fetchone()
    assert tuple(row) == ("pending", None)


def test_busy_database_returns_retryable_error(db_path, conn, users):
    fast_app = create_app(db_path=db_path, demo_mode=True, session_secret="test-secret",
                          seed_requests=False, busy_timeout_ms=50)
    olivia = UserClient(fast_app, users["olivia"])
    token = olivia.csrf()
    blocker = connect(db_path)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        short = connect(db_path, busy_timeout_ms=50)
        with pytest.raises(DatabaseBusy):
            services.create_request(short, users["olivia"], "100", "x")
        short.close()
        r = olivia.client.post("/requests", data={"csrf_token": token, "amount": "1.00", "reason": "x"})
        assert r.status_code == 503
        assert r.headers["retry-after"] == "1"
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    assert conn.execute("SELECT COUNT(*) FROM refund_requests").fetchone()[0] == 0
    assert created_id(olivia.submit()) == 1


def test_foreign_keys_and_consistency_enforced(conn, users):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO refund_requests (amount_cents, reason, submitted_by, created_at)"
                     " VALUES (100, 'x', 9999, 'now')")
    rid = services.create_request(conn, users["olivia"], "100", "x")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE refund_requests SET status='approved' WHERE id=?", (rid,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE refund_requests SET decided_by=? , decision_reason='r', decided_at='t'"
                     " WHERE id=?", (users["raj"]["id"], rid))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE refund_requests SET status='approved', decided_by=?, decision_reason='r',"
                     " decided_at='t' WHERE id=?", (users["olivia"]["id"], rid))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE refund_requests SET reason='  ' WHERE id=?", (rid,))
