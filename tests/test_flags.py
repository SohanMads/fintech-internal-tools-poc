import pytest

from app import audit, flags
from app.errors import Conflict, Forbidden

from .conftest import created_id


def flag_row(conn, key="csv-export"):
    return conn.execute("SELECT * FROM feature_flags WHERE key=?", (key,)).fetchone()


def flag_events(conn, flag_id):
    return conn.execute("SELECT COUNT(*) FROM audit_events WHERE flag_id=?", (flag_id,)).fetchone()[0]


def change(client, flag, reason="Synthetic test change", version=None, desired=None):
    return client.post(f"/flags/{flag['id']}", {
        "desired": desired or ("disable" if flag["enabled"] else "enable"),
        "reason": reason,
        "version": str(flag["version"] if version is None else version),
    })


def test_everyone_can_view_flags_and_not_live_label(as_user):
    for name in ("olivia", "rita", "victor", "fiona"):
        r = as_user(name).get("/flags")
        assert r.status_code == 200
        assert "not connected to a live application" in r.text
        assert "CSV export" in r.text


def test_flag_admin_changes_flag_with_audit(as_user, conn):
    flag = flag_row(conn)
    assert change(as_user("fiona"), flag).status_code == 303
    updated = flag_row(conn)
    assert updated["enabled"] == 0 and updated["version"] == flag["version"] + 1
    event = conn.execute("SELECT * FROM audit_events WHERE flag_id=?", (flag["id"],)).fetchone()
    assert (event["action"], event["from_status"], event["to_status"], event["request_id"]) == \
        ("flag_disabled", "enabled", "disabled", None)
    assert event["actor_id"] == updated["updated_by"]


@pytest.mark.parametrize("name", ["olivia", "rita", "raj", "victor"])
def test_non_admins_cannot_edit_flags(as_user, conn, name):
    flag = flag_row(conn)
    r = change(as_user(name), flag)
    assert r.status_code == 403
    assert flag_row(conn)["version"] == flag["version"]
    assert flag_events(conn, flag["id"]) == 0
    assert "Read-only" in as_user(name).get(f"/flags/{flag['id']}").text


def test_anonymous_cannot_view_or_edit_flags(as_user, conn):
    anon = as_user()
    assert anon.get("/flags").status_code in (303, 401)
    assert change(anon, flag_row(conn)).status_code == 401


def test_flag_admin_cannot_decide_or_submit_refunds(as_user):
    rid = created_id(as_user("olivia").submit())
    fiona = as_user("fiona")
    assert fiona.decide(rid).status_code == 403
    assert fiona.submit().status_code == 403


def test_flag_change_requires_reason(as_user, conn):
    flag = flag_row(conn)
    assert change(as_user("fiona"), flag, reason="   ").status_code == 422
    assert flag_row(conn)["enabled"] == flag["enabled"]
    assert flag_events(conn, flag["id"]) == 0


def test_flag_change_requires_csrf(as_user, conn):
    flag = flag_row(conn)
    fiona = as_user("fiona")
    data = {"desired": "disable", "reason": "x", "version": str(flag["version"])}
    assert fiona.post(f"/flags/{flag['id']}", data, csrf=False).status_code == 403
    assert fiona.client.post(f"/flags/{flag['id']}", data={**data, "csrf_token": "forged"}).status_code == 403
    assert flag_row(conn)["version"] == flag["version"]


def test_get_cannot_change_flag(as_user, conn):
    flag = flag_row(conn)
    as_user("fiona").get(f"/flags/{flag['id']}?desired=disable&reason=x&version=1")
    assert flag_row(conn)["version"] == flag["version"]


def test_stale_form_is_rejected(as_user, conn):
    fiona = as_user("fiona")
    stale = flag_row(conn)  # both forms loaded at version 1, flag enabled
    assert change(fiona, stale, desired="disable").status_code == 303
    assert change(fiona, flag_row(conn), desired="enable").status_code == 303  # newer change: enabled again
    r = change(fiona, stale, desired="disable")  # old form would overwrite the newer change
    assert r.status_code == 409
    assert flag_row(conn)["enabled"] == 1 and flag_row(conn)["version"] == stale["version"] + 2
    assert flag_events(conn, stale["id"]) == 2


def test_stale_update_rejected_at_service_level(conn, users):
    flag = flag_row(conn)
    flags.set_flag(conn, users["fiona"], flag["id"], "disable", "first", flag["version"])
    with pytest.raises(Conflict):
        flags.set_flag(conn, users["fiona"], flag["id"], "disable", "second", flag["version"])
    with pytest.raises(Forbidden):
        flags.set_flag(conn, users["rita"], flag["id"], "enable", "x", flag["version"] + 1)


def test_unknown_flag_is_404(as_user):
    fiona = as_user("fiona")
    assert fiona.get("/flags/999").status_code == 404
    assert fiona.post("/flags/999", {"desired": "enable", "reason": "x", "version": "1"}).status_code == 404


def test_audit_failure_rolls_back_flag_change(conn, users, monkeypatch):
    flag = flag_row(conn)

    def boom(*a, **k):
        raise RuntimeError("audit write failed")
    monkeypatch.setattr(audit, "record", boom)
    with pytest.raises(RuntimeError):
        flags.set_flag(conn, users["fiona"], flag["id"], "disable", "x", flag["version"])
    after = flag_row(conn)
    assert (after["enabled"], after["version"], after["updated_by"]) == (flag["enabled"], flag["version"], None)
    assert flag_events(conn, flag["id"]) == 0


def test_audit_rows_must_target_exactly_one_tool(conn, users):
    import sqlite3
    flag = flag_row(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO audit_events (request_id, flag_id, actor_id, action, to_status, reason, created_at)"
                     " VALUES (NULL, NULL, ?, 'flag_enabled', 'enabled', 'x', 'now')", (users["fiona"]["id"],))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO audit_events (request_id, flag_id, actor_id, action, to_status, reason, created_at)"
                     " VALUES (NULL, ?, ?, 'approved', 'approved', 'x', 'now')", (flag["id"], users["fiona"]["id"]))
