import re

import pytest

from fastapi.testclient import TestClient

from app import refunds as services
from app.db import connect
from app.main import create_app

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def app(db_path):
    return create_app(db_path=db_path, demo_mode=True, session_secret="test-secret", seed_requests=False)


@pytest.fixture
def conn(app, db_path):
    c = connect(db_path)
    yield c
    c.close()


@pytest.fixture
def users(conn):
    return {u["username"]: u for u in services.list_users(conn)}


def csrf_of(client, path="/login"):
    return CSRF_RE.search(client.get(path).text).group(1)


class UserClient:
    def __init__(self, app, user=None):
        self.client = TestClient(app, follow_redirects=False)
        if user is not None:
            token = csrf_of(self.client)
            r = self.client.post("/login", data={"csrf_token": token, "user_id": str(user["id"])})
            assert r.status_code == 303

    def csrf(self):
        return csrf_of(self.client, "/login")

    def get(self, path, **kw):
        return self.client.get(path, **kw)

    def post(self, path, data=None, csrf=True):
        data = dict(data or {})
        if csrf:
            data["csrf_token"] = self.csrf()
        return self.client.post(path, data=data)

    def submit(self, amount="10.00", reason="Synthetic refund"):
        return self.post("/requests", {"amount": amount, "reason": reason})

    def decide(self, request_id, decision="approve", reason="Checked"):
        return self.post(f"/requests/{request_id}/decision", {"decision": decision, "reason": reason})


@pytest.fixture
def as_user(app, users):
    def make(username=None):
        return UserClient(app, users[username] if username else None)
    return make


def created_id(response):
    assert response.status_code == 303, response.text
    return int(response.headers["location"].rsplit("/", 1)[1])
