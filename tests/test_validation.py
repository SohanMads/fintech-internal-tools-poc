import pytest

from .conftest import created_id


def counts(conn):
    return tuple(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                 for t in ("refund_requests", "audit_events"))


@pytest.mark.parametrize("amount", ["0", "0.00", "-5", "10.505", "12.", ".5", "1,000", "$5", "abc", "", "1e3", "NaN", "1000000.01", " "])
def test_invalid_amount_rejected(as_user, conn, amount):
    r = as_user("olivia").submit(amount=amount)
    assert r.status_code == 422
    assert counts(conn) == (0, 0)


@pytest.mark.parametrize("reason", ["", "   ", "\n\t"])
def test_blank_reason_rejected(as_user, conn, reason):
    assert as_user("olivia").submit(reason=reason).status_code == 422
    assert counts(conn) == (0, 0)


def test_reason_is_trimmed(as_user, conn):
    rid = created_id(as_user("olivia").submit(amount=" 12.50 ", reason="  Duplicate charge  "))
    row = conn.execute("SELECT amount_cents, reason FROM refund_requests WHERE id=?", (rid,)).fetchone()
    assert tuple(row) == (1250, "Duplicate charge")


@pytest.mark.parametrize("data", [{"decision": "approve", "reason": "  "},
                                  {"decision": "approve"},
                                  {"decision": "escalate", "reason": "ok"}])
def test_invalid_decision_rejected(as_user, conn, data):
    rid = created_id(as_user("olivia").submit())
    assert as_user("raj").post(f"/requests/{rid}/decision", data).status_code == 422
    assert conn.execute("SELECT status FROM refund_requests WHERE id=?", (rid,)).fetchone()[0] == "pending"
    assert counts(conn) == (1, 1)


def test_approved_label_shown(as_user):
    rid = created_id(as_user("olivia").submit())
    raj = as_user("raj")
    assert raj.decide(rid, "approve").status_code == 303
    assert "Approved — payment not executed." in raj.get(f"/requests/{rid}").text
    assert "Approved — payment not executed." in raj.get("/requests?status=approved").text


def test_status_filter(as_user):
    olivia = as_user("olivia")
    a = created_id(olivia.submit(reason="first"))
    b = created_id(olivia.submit(reason="second"))
    as_user("raj").decide(b, "reject", "no")
    pending = olivia.get("/requests?status=pending").text
    rejected = olivia.get("/requests?status=rejected").text
    assert f"/requests/{a}\"" in pending and f"/requests/{b}\"" not in pending
    assert f"/requests/{b}\"" in rejected and f"/requests/{a}\"" not in rejected
