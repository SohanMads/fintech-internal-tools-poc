import re
import sqlite3

from . import audit, identity
from .db import transaction
from .validation import MAX_REASON_LENGTH, clean_reason  # noqa: F401
from .errors import Conflict, Forbidden, NotFound, ServiceError, ValidationFailed  # noqa: F401

MAX_AMOUNT_CENTS = 100_000_000  # $1,000,000.00

SUBMIT_ROLES = {"operator", "reviewer"}
DECIDE_ROLES = {"reviewer"}
STATUSES = ("pending", "approved", "rejected")
DECISIONS = {"approve": "approved", "reject": "rejected"}

_DOLLARS_RE = re.compile(r"^([0-9]+)(?:\.([0-9]{1,2}))?$")


def get_user(conn, user_id):
    return identity.get_user(conn, user_id)


def list_users(conn):
    return identity.list_users(conn)


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
    """Parse a dollar string such as "12.50" into integer cents using only string and int operations."""
    text = raw.strip() if isinstance(raw, str) else ""
    match = _DOLLARS_RE.match(text)
    if not match:
        raise ValueError("Enter a dollar amount like 12.50 (digits, at most two decimal places).")
    dollars, cents = match.group(1), (match.group(2) or "").ljust(2, "0")
    value = int(dollars) * 100 + int(cents)
    if value <= 0:
        raise ValueError("Amount must be greater than zero.")
    if value > MAX_AMOUNT_CENTS:
        raise ValueError("Amount must not exceed $1,000,000.00.")
    return value


def create_request(conn: sqlite3.Connection, actor, amount_raw, reason_raw) -> int:
    if not can_submit(actor):
        raise Forbidden("Your role is not allowed to submit refund requests.")
    errors = {}
    try:
        amount = parse_amount_cents(amount_raw)
    except ValueError as exc:
        errors["amount"] = str(exc)
    try:
        reason = clean_reason(reason_raw)
    except ValueError as exc:
        errors["reason"] = str(exc)
    if errors:
        raise ValidationFailed("Please correct the errors below.", errors)

    at = audit.now()
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO refund_requests (amount_cents, reason, status, submitted_by, created_at)"
            " VALUES (?, ?, 'pending', ?, ?)",
            (amount, reason, actor["id"], at),
        )
        request_id = cur.lastrowid
        audit.record(conn, request_id=request_id, actor_id=actor["id"], action="created",
                     from_status=None, to_status="pending", reason=reason, at=at)
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

    at = audit.now()
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
            raise Conflict("Another reviewer's decision was saved first, so yours was not applied.",
                           title="This request has already been decided",
                           back_url=f"/requests/{request_id}", back_label="View the current decision")
        audit.record(conn, request_id=request_id, actor_id=actor["id"], action=new_status,
                     from_status="pending", to_status=new_status, reason=reason, at=at)
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
    return audit.history(conn, request_id=request_id)


def seed_demo_requests(conn: sqlite3.Connection) -> None:
    """Add synthetic requests when the queue is empty."""
    if conn.execute("SELECT COUNT(*) FROM refund_requests").fetchone()[0]:
        return
    users = {u["username"]: u for u in list_users(conn)}
    create_request(conn, users["olivia"], "49.99", "Synthetic: duplicate charge on demo order DEMO-1001")
    create_request(conn, users["olivia"], "1250.00", "Synthetic: service outage credit for demo account DEMO-2002")
    rid = create_request(conn, users["rita"], "15.00", "Synthetic: shipping fee refund for demo order DEMO-3003")
    decide(conn, users["raj"], rid, "approve", "Synthetic: verified against demo shipping log")
