# fintech-internal-tools-poc

Refund-request approval prototype. An operations employee submits a refund request, a **different** authorized reviewer approves or rejects it, and every creation and decision is stored with an audit history.

> **Local prototype only.** Synthetic data. **No payments are executed**: "Approved — payment not executed." is a status change in SQLite and nothing else.

## Run locally

Requires Python 3.10+.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt

DEMO_MODE=true uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
# open http://127.0.0.1:8000
```

Run tests:

```bash
python -m pytest -q
```

| Env var | Default | Purpose |
|---|---|---|
| `DEMO_MODE` | unset | Must be `true` to enable the demo identity picker. Otherwise every request gets **503** (fail closed — there is no real authentication). |
| `DATABASE_PATH` | `refunds.db` | SQLite file (created and seeded on startup). |
| `SESSION_SECRET` | random per process | Signs the session cookie. If unset, sessions reset when the server restarts. |

Delete `refunds.db*` to reset the demo data.

## Demo identities

**Demo identity — not production authentication.** The login page lets anyone pick any seeded user, i.e. the demo allows impersonation by design. It exists to demonstrate the authorization rules under a simulated identity; it is not secure authentication. Keep it on localhost.

| User | Role | Submit | Approve / reject |
|---|---|---|---|
| Olivia Operator | operator | yes | no |
| Rita Reviewer | reviewer | yes | yes, except own requests |
| Raj Reviewer | reviewer | yes | yes, except own requests |
| Victor Viewer | viewer | no (read-only) | no |

The session stores only the user id. The role and the audit actor are always read from the server-side `users` table; client-supplied role/actor fields are ignored.

## Rules enforced on the server

- Amount is a positive integer number of USD cents (digits only, max 100,000,000). Reasons are trimmed, non-blank, ≤ 1000 chars.
- Only reviewers decide, never on their own requests; a decision needs a reason and only changes a `pending` request.
- Responses: **404** nonexistent request, **403** forbidden action or bad CSRF token, **409** already decided, **422** validation error, **401** no identity on a POST, **503** + `Retry-After: 1` if the SQLite busy timeout (2 s) expires.
- All state-changing actions (submit, decide, choose identity, sign out) are `POST` forms with a per-session CSRF token (Starlette `SessionMiddleware` signed cookie + synchronizer token, compared with `secrets.compare_digest`). No `GET` request mutates anything.

## Architecture

One FastAPI app, server-rendered Jinja2 templates, SQLite.

```
app/db.py        connection setup (foreign_keys=ON, busy_timeout), schema, seed users, transaction helper
app/services.py  domain rules: validation, permissions, create/decide, audit writes
app/main.py      routes, demo identity, session + CSRF, error handling
app/templates/   login, queue (status filter), new request, detail (+ audit history), error
tests/           pytest suite (permissions, validation, conflicts, audit consistency, DB integrity)
```

### Schema

- `users(id, username UNIQUE, display_name, role CHECK IN (operator, reviewer, viewer))`
- `refund_requests(id, amount_cents CHECK integer > 0, reason CHECK trimmed non-blank, status CHECK IN (pending, approved, rejected), submitted_by FK, created_at, decided_by FK, decision_reason, decided_at)`
  - CHECK: pending ⇒ no decision fields; approved/rejected ⇒ reviewer, reason and timestamp all present.
  - CHECK: `decided_by <> submitted_by`.
- `audit_events(id, request_id FK, actor_id FK, action CHECK IN (created, approved, rejected), from_status, to_status, reason, created_at)`

Foreign keys are enabled on every connection.

### Atomicity and conflicts

Each create or decision runs in one `BEGIN IMMEDIATE` transaction that writes the request row **and** its audit event; if either fails, both roll back. Decisions use a conditional update:

```sql
UPDATE refund_requests SET status = ?, decided_by = ?, decision_reason = ?, decided_at = ?
WHERE id = ? AND status = 'pending' AND submitted_by <> ?
```

If two reviewers decide the same request, the **first decision to commit** wins (not necessarily the first click); the other gets 409 and no audit event is written for it.

### Audit history

The application only ever inserts into `audit_events`; it has no code path that updates or deletes audit rows. This is application behavior only — the table is **not** immutable or tamper-resistant against anyone with direct access to the SQLite file.

## Simulated vs. out of scope

- **Simulated:** login (identity picker, impersonation allowed), users and requests (synthetic), approval (status change only, no money moves).
- **Out of scope:** production SSO/authentication, real payment/processor/ledger integrations, public deployment, multi-instance concurrency (single SQLite file), microservices, a generic workflow builder, notifications, editing/withdrawing requests, pagination.
