from fastapi.testclient import TestClient

from app.main import create_app

from .conftest import created_id


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_fail_closed_without_demo_mode(tmp_path):
    app = create_app(db_path=str(tmp_path / "x.db"), demo_mode=False)
    client = TestClient(app)
    for path in ("/login", "/requests", "/requests/1"):
        assert client.get(path).status_code == 503
    assert client.post("/login", data={"user_id": "1"}).status_code == 503
    assert not (tmp_path / "x.db").exists()


def test_login_page_labels_demo_identity(as_user):
    assert "Demo identity. Not production authentication." in as_user().get("/login").text


def test_unauthenticated_cannot_view_or_mutate(as_user, conn):
    anon = as_user()
    assert anon.get("/requests").status_code == 303
    assert anon.submit().status_code == 401
    assert count(conn, "refund_requests") == 0


def test_operator_and_reviewer_can_submit(as_user):
    for name in ("olivia", "rita"):
        assert created_id(as_user(name).submit())


def test_viewer_cannot_submit_but_can_read(as_user, conn):
    rid = created_id(as_user("olivia").submit())
    viewer = as_user("victor")
    assert viewer.get("/requests/new").status_code == 403
    assert viewer.submit().status_code == 403
    assert viewer.get("/requests").status_code == 200
    assert viewer.get(f"/requests/{rid}").status_code == 200
    assert count(conn, "refund_requests") == 1


def test_only_reviewers_can_decide(as_user, conn):
    rid = created_id(as_user("rita").submit())
    assert as_user("olivia").decide(rid).status_code == 403
    assert as_user("victor").decide(rid).status_code == 403
    assert conn.execute("SELECT status FROM refund_requests WHERE id=?", (rid,)).fetchone()[0] == "pending"
    assert as_user("raj").decide(rid).status_code == 303


def test_self_approval_forbidden(as_user, conn):
    rita = as_user("rita")
    rid = created_id(rita.submit())
    page = rita.get(f"/requests/{rid}").text
    assert 'value="approve"' not in page
    for decision in ("approve", "reject"):
        assert rita.decide(rid, decision).status_code == 403
    row = conn.execute("SELECT status, decided_by FROM refund_requests WHERE id=?", (rid,)).fetchone()
    assert tuple(row) == ("pending", None)
    assert count(conn, "audit_events") == 1


def test_client_supplied_role_and_actor_are_ignored(as_user, conn, users):
    viewer = as_user("victor")
    r = viewer.post("/requests", {"amount": "1.00", "reason": "x", "role": "reviewer",
                                  "submitted_by": str(users["olivia"]["id"])})
    assert r.status_code == 403
    olivia = as_user("olivia")
    rid = created_id(olivia.post("/requests", {"amount": "1.00", "reason": "x",
                                                "submitted_by": str(users["rita"]["id"]),
                                                "actor_id": str(users["rita"]["id"])}))
    row = conn.execute("SELECT submitted_by FROM refund_requests WHERE id=?", (rid,)).fetchone()
    assert row[0] == users["olivia"]["id"]
    actor = conn.execute("SELECT actor_id FROM audit_events WHERE request_id=?", (rid,)).fetchone()
    assert actor[0] == users["olivia"]["id"]


def test_csrf_required_for_mutations(as_user, conn, users):
    olivia = as_user("olivia")
    assert olivia.post("/requests", {"amount": "1.00", "reason": "x"}, csrf=False).status_code == 403
    r = olivia.client.post("/requests", data={"amount": "1.00", "reason": "x", "csrf_token": "forged"})
    assert r.status_code == 403
    assert count(conn, "refund_requests") == 0
    anon = as_user()
    assert anon.post("/login", {"user_id": str(users["rita"]["id"])}, csrf=False).status_code == 403
    assert anon.post("/logout", csrf=False).status_code == 403


def test_get_never_mutates(as_user):
    olivia = as_user("olivia")
    rid = created_id(olivia.submit())
    assert as_user("raj").get(f"/requests/{rid}/decision").status_code == 405
    assert olivia.get("/logout").status_code == 405


def test_unknown_request_is_404(as_user):
    raj = as_user("raj")
    assert raj.get("/requests/999").status_code == 404
    assert raj.decide(999).status_code == 404
