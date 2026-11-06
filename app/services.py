import re
import sqlite3
from datetime import datetime, timezone

from .db import transaction

MAX_AMOUNT_CENTS = 100_000_000  # $1,000,000.00
MAX_REASON_LENGTH = 1000

SUBMIT_ROLES = {"operator", "reviewer"}
DECIDE_ROLES = {"reviewer"}
STATUSES = ("pending", "approved", "rejected")
DECISIONS = {"approve": "approved", "reject": "rejected"}

_AMOUNT_RE = re.compile(r"^[0-9]+$")


class ServiceError(Exception):
    status_code = 400

    def __init__(self, message: str, errors: dict | None = None):
        super().__init__(message)
        self.message = message
        self.errors = errors or {}


class ValidationFailed(ServiceError):
    status_code = 422


class Forbidden(ServiceError):
    status_code = 403


class NotFound(ServiceError):
    status_code = 404


class Conflict(ServiceError):
    status_code = 409


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_user(conn: sqlite3.Connection, user_id) -> sqlite3.Row | None:
    if user_id is None:
        return None
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def list_users(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM users ORDER BY id").fetchall()


def can_submit(user) -> bool:
    return user is not None and user["role"] in SUBMIT_ROLES


def can_decide(user, request_row) -> bool:
    return (
        user is not None
        and user["role"] in DECIDE_ROLES
        and request_row["status"] == "pending"
        and request_row["submitted_by"] != user["id"]
    )


def parse_amount_cents(raw) -> int:
    text = (raw or "").strip() if isinstance(raw, str) else ""
    if not _AMOUNT_RE.match(text):
        raise ValueError("Amount must be a whole number of cents (digits only, e.g. 1250 for $12.50).")
    value = int(text)
    if value <= 0:
        raise ValueError("Amount must be greater than zero.")
    if value > MAX_AMOUNT_CENTS:
        raise ValueError(f"Amount must not exceed {MAX_AMOUNT_CENTS} cents.")
    return value


def clean_reason(raw) -> str:
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        raise ValueError("A reason is required.")
    if len(text) > MAX_REASON_LENGTH:
        raise ValueError(f"Reason must be at most {MAX_REASON_LENGTH} characters.")
    return text


def _insert_audit(conn, request_id, actor_id, action, from_status, to_status, reason, at):
    conn.execute(
        "INSERT INTO audit_events (request_id, actor_id, action, from_status, to_status, reason, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (request_id, actor_id, action, from_status, to_status, reason, at),
    )


def create_request(conn: sqlite3.Connection, actor, amount_raw, reason_raw) -> int:
    if not can_submit(actor):
        raise Forbidden("Your role is not allowed to submit refund requests.")
    errors = {}
    try:
        amount = parse_amount_cents(amount_raw)
    except ValueError as exc:
        errors["amount_cents"] = str(exc)
    try:
        reason = clean_reason(reason_raw)
    except ValueError as exc:
        errors["reason"] = str(exc)
    if errors:
        raise ValidationFailed("Please correct the errors below.", errors)

    at = _now()
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO refund_requests (amount_cents, reason, status, submitted_by, created_at)"
            " VALUES (?, ?, 'pending', ?, ?)",
            (amount, reason, actor["id"], at),
        )
        request_id = cur.lastrowid
        _insert_audit(conn, request_id, actor["id"], "created", None, "pending", reason, at)
    return request_id


def decide(conn: sqlite3.Connection, actor, request_id: int, decision, reason_raw) -> str:
    if actor is None or actor["role"] not in DECIDE_ROLES:
        raise Forbidden("Only reviewers can approve or reject refund requests.")
    new_status = DECISIONS.get(decision)
    errors = {}
    if new_status is None:
        errors["decision"] = "Decision must be approve or reject."
    try:
        reason = clean_reason(reason_raw)
    except ValueError as exc:
        errors["reason"] = str(exc)
    if errors:
        raise ValidationFailed("Please correct the errors below.", errors)

    at = _now()
    with transaction(conn):
        row = conn.execute(
            "SELECT id, status, submitted_by FROM refund_requests WHERE id = ?", (request_id,)
        ).fetchone()
        if row is None:
            raise NotFound("Refund request not found.")
        if row["submitted_by"] == actor["id"]:
            raise Forbidden("You cannot decide a refund request you submitted.")
        cur = conn.execute(
            "UPDATE refund_requests"
            " SET status = ?, decided_by = ?, decision_reason = ?, decided_at = ?"
            " WHERE id = ? AND status = 'pending' AND submitted_by <> ?",
            (new_status, actor["id"], reason, at, request_id, actor["id"]),
        )
        if cur.rowcount != 1:
            raise Conflict("This refund request has already been decided.")
        _insert_audit(conn, request_id, actor["id"], new_status, "pending", new_status, reason, at)
    return new_status


def list_requests(conn: sqlite3.Connection, status: str | None = None) -> list[sqlite3.Row]:
    sql = (
        "SELECT r.*, s.display_name AS submitter_name, d.display_name AS decider_name"
        " FROM refund_requests r"
        " JOIN users s ON s.id = r.submitted_by"
        " LEFT JOIN users d ON d.id = r.decided_by"
    )
    params: tuple = ()
    if status in STATUSES:
        sql += " WHERE r.status = ?"
        params = (status,)
    sql += " ORDER BY r.id DESC"
    return conn.execute(sql, params).fetchall()


def get_request(conn: sqlite3.Connection, request_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT r.*, s.display_name AS submitter_name, d.display_name AS decider_name"
        " FROM refund_requests r"
        " JOIN users s ON s.id = r.submitted_by"
        " LEFT JOIN users d ON d.id = r.decided_by"
        " WHERE r.id = ?",
        (request_id,),
    ).fetchone()


def audit_history(conn: sqlite3.Connection, request_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT a.*, u.display_name AS actor_name, u.role AS actor_role"
        " FROM audit_events a JOIN users u ON u.id = a.actor_id"
        " WHERE a.request_id = ? ORDER BY a.id",
        (request_id,),
    ).fetchall()


def seed_demo_requests(conn: sqlite3.Connection) -> None:
    """Add synthetic requests when the queue is empty."""
    if conn.execute("SELECT COUNT(*) FROM refund_requests").fetchone()[0]:
        return
    users = {u["username"]: u for u in list_users(conn)}
    create_request(conn, users["olivia"], "4999", "Synthetic: duplicate charge on demo order DEMO-1001")
    create_request(conn, users["olivia"], "125000", "Synthetic: service outage credit for demo account DEMO-2002")
    rid = create_request(conn, users["rita"], "1500", "Synthetic: shipping fee refund for demo order DEMO-3003")
    decide(conn, users["raj"], rid, "approve", "Synthetic: verified against demo shipping log")
