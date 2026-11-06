# fintech-internal-tools-poc

**Goal of this repository.** We are evaluating whether engineers can use Devin to build and maintain internal applications that would otherwise be built with Power Apps. The refund application is a representative first workflow. We are **not** building a refund business, a payment processor, or a complete low-code platform.

Two small tools share one local FastAPI app ("Internal Operations"):

- **Refunds** (primary end-to-end workflow): an operations employee submits a refund request, a **different** authorized reviewer approves or rejects it, and every creation and decision is stored with an audit history.
- **Sandbox Flags** (reuse test): a flag administrator enables/disables synthetic feature flags with a required reason; everyone else can only view them. **These settings are not connected to a live application.**

> **Local prototype only.** Synthetic data. **No payments are executed**: "Approved — payment not executed." is a status change in SQLite and nothing else.

Two demo tools do not show that Power Apps can be fully replaced, and they do not establish the cost of building or maintaining ten future applications.

## Run locally

Requires Python 3.10+.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt

export DEMO_MODE=true                                                  # required; without it every page returns 503
export SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"  # optional; keeps sessions across restarts
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
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

Delete `refunds.db*` to reset the demo data. An existing `refunds.db` from the refund-only version is migrated in place on startup (see [Audit schema change](#audit-schema-change-and-tradeoff)).

### Demo script

1. Open http://127.0.0.1:8000 and choose **Olivia Operator**. **Refunds → + Submit a refund request**, amount `12.50`, any reason. (`12.505` or `abc` is rejected.)
2. Switch identity to **Rita Reviewer**, open the new request, enter a reason and **Approve** → "Approved — payment not executed."
3. Open the same pending request as Rita and Raj in two browsers and decide in both: the second gets **409**.
4. Choose **Fiona Flag Admin** → **Sandbox Flags** → a flag → enter a reason → **Enable/Disable**. The audit history shows the change.
5. Open the same flag in two Fiona tabs and submit both: the older form gets **409** instead of overwriting.
6. As any other user, **Sandbox Flags** is read-only. As Fiona, refund decision buttons are absent and **/requests/new** returns 403.

## Demo identities

**Demo identity — not production authentication.** The login page lets anyone pick any seeded user, i.e. the demo allows impersonation by design. It exists to demonstrate the authorization rules under a simulated identity; it is not secure authentication. Keep it on localhost.

| User | Role | Submit | Approve / reject |
|---|---|---|---|
| Olivia Operator | operator | yes | no |
| Rita Reviewer | reviewer | yes | yes, except own requests |
| Raj Reviewer | reviewer | yes | yes, except own requests |
| Victor Viewer | viewer | no (read-only) | no |
| Fiona Flag Admin | viewer + `flags.edit` grant | no | no |

Only Fiona can change Sandbox Flags; everyone signed in can view them. The session stores only the user id. The refund role, the `flags.edit` grant (`tool_permissions` table) and the audit actor are always read from the server-side database; client-supplied role/actor fields are ignored.

## Rules enforced on the server

- Refund amount is entered in dollars (`12.50`, `12.5`, `7`) and converted to integer cents with string/integer arithmetic only (no `float`, no binary floating point). Rejected: more than two decimals, `12.`, `.5`, signs, `$`, commas, exponents, zero, over $1,000,000.00. Stored as `amount_cents` INTEGER.
- Reasons (refund submissions, decisions, flag changes) are trimmed, non-blank, ≤ 1000 chars.
- Only reviewers decide, never on their own requests; a decision needs a reason and only changes a `pending` request.
- Flag changes need the `flags.edit` grant, a reason, and the flag `version` the form was rendered with; a mismatched version is **409** (stale form).
- Responses: **404** nonexistent request or flag, **403** forbidden action or bad CSRF token, **409** already decided / stale flag form, **422** validation error, **401** no identity on a POST, **503** + `Retry-After: 1` if the SQLite busy timeout (2 s) expires.
- All state-changing actions (submit, decide, change flag, choose identity, sign out) are `POST` forms with a per-session CSRF token (Starlette `SessionMiddleware` signed cookie + synchronizer token, compared with `secrets.compare_digest`). No `GET` request mutates anything.

## Architecture

One FastAPI process, server-rendered Jinja2 templates, one SQLite file. No extra services.

```mermaid
flowchart TB
  Browser["Browser (POST forms + CSRF token)"] --> Main
  subgraph Shared["Shared foundation"]
    Main["app/main.py<br/>routes, demo identity + session, CSRF, error pages, nav"]
    Identity["app/identity.py<br/>users + tool_permissions lookup"]
    DB["app/db.py<br/>connect (foreign_keys, busy_timeout), schema, migration, seeds, transaction()"]
    Audit["app/audit.py<br/>record() / history()"]
    Errors["app/errors.py + app/validation.py<br/>404/403/409/422 errors, reason rules"]
    Layout["templates/base.html, login.html, error.html"]
  end
  subgraph Refunds["Refunds module"]
    R["app/refunds.py<br/>dollars→cents, submit/decide rules, no self-approval"]
    RT["queue / new / detail templates"]
  end
  subgraph Flags["Sandbox Flags module"]
    F["app/flags.py<br/>flags.edit check, enable/disable, version check"]
    FT["flags / flag_detail templates"]
  end
  Main --> R & F
  R & F --> Identity & Audit & Errors & DB
  DB --> SQLite[("SQLite file")]
```

```
app/main.py        routes for both tools, demo identity, session + CSRF, error handling, nav context
app/db.py          connection setup, schema, audit migration, seeds, transaction helper
app/identity.py    server-side user and grant lookup
app/audit.py       shared audit insert + history query
app/errors.py      shared ServiceError hierarchy (status codes)
app/validation.py  shared reason validation
app/refunds.py     refund-only rules
app/flags.py       flag-only rules
app/templates/     base (Internal Operations nav), login, error, refund pages, flag pages
tests/             pytest suite
```

### Development flow

```mermaid
flowchart LR
  Req["Written requirements"] --> Devin["Devin session<br/>plan → code → tests → browser check"]
  Devin --> PR["GitHub PR"]
  PR --> Review["Human review + local test run"]
  Review -->|changes requested| Devin
```

CI: `.github/workflows/tests.yml` runs `python -m pytest -q` on Python 3.10 for pull requests and pushes to `main`. Tests use a temporary SQLite file per test and a fixed test session secret; no credentials or deployment.

### Schema

- `users(id, username UNIQUE, display_name, role CHECK IN (operator, reviewer, viewer))`
- `tool_permissions(user_id FK, permission CHECK IN ('flags.edit'), PK(user_id, permission))`
- `refund_requests(id, amount_cents CHECK integer > 0, reason CHECK trimmed non-blank, status CHECK IN (pending, approved, rejected), submitted_by FK, created_at, decided_by FK, decision_reason, decided_at)`
  - CHECK: pending ⇒ no decision fields; approved/rejected ⇒ reviewer, reason and timestamp all present.
  - CHECK: `decided_by <> submitted_by`.
- `feature_flags(id, key UNIQUE, name, description, enabled CHECK IN (0,1), version ≥ 1, updated_at, updated_by FK)`
- `audit_events(id, request_id FK NULL, flag_id FK NULL, actor_id FK, action, from_status, to_status, reason, created_at)`
  - CHECK: exactly one of `request_id` / `flag_id` is set, and the action matches the tool (`created/approved/rejected` for refunds, `flag_enabled/flag_disabled` for flags).

Foreign keys are enabled on every connection.

### Audit schema change and tradeoff

The refund-only `audit_events` had `request_id NOT NULL`. The change: make `request_id` nullable, add nullable `flag_id`, and add a CHECK that exactly one target is set. SQLite cannot relax `NOT NULL` in place, so `init_db` rebuilds the table once (create new → copy all rows with `flag_id = NULL` → drop → rename) in one transaction, verifies the row count and `PRAGMA foreign_key_check`, and rolls back if either fails. Existing ids and history are kept. This was checked on a test database and on the existing local demo database (7 refund audit rows before and after).

- **Why this:** one audit table and one `audit.record()` / `audit.history()` for both tools, with real foreign keys to each target table.
- **Cost:** this keeps real relational references for these two tools, but each additional audited resource type needs a schema change (a new nullable FK column, a wider CHECK, and a table rebuild in SQLite). It can support more tools; it just requires a migration for each one. Alternatives: a separate audit table per tool (no migration, but duplicated audit code), or a generic `target_type` + `target_id` pair (scales, but loses foreign-key enforcement). For two tools, the nullable-FK version was the smallest change that kept integrity checks.

### Atomicity and conflicts

Each create or decision runs in one `BEGIN IMMEDIATE` transaction that writes the request row **and** its audit event; if either fails, both roll back. Decisions use a conditional update:

```sql
UPDATE refund_requests SET status = ?, decided_by = ?, decision_reason = ?, decided_at = ?
WHERE id = ? AND status = 'pending' AND submitted_by <> ?
```

If two reviewers decide the same request, the **first decision to commit** wins (not necessarily the first click); the other gets 409 and no audit event is written for it.

Flag changes use the same pattern: one transaction updates the flag with `WHERE id = ? AND version = ?` (the version from the form), increments `version`, and inserts the audit event. A form rendered before a newer change carries an old version and gets 409.

### Audit history

The application only ever inserts into `audit_events`; it has no code path that updates or deletes audit rows. This is application behavior only — the table is **not** immutable or tamper-resistant against anyone with direct access to the SQLite file.

## What Sandbox Flags reused vs. newly implemented

**Reused unchanged or with small generalization:**
- Demo identity picker, session cookie, `DEMO_MODE` fail-closed middleware, sign-in/out.
- CSRF synchronizer token and `checked_form()`; `require_user()`.
- `db.connect()` (foreign keys, busy timeout), `transaction()` (`BEGIN IMMEDIATE`), `DatabaseBusy` → 503 handler.
- Audit insert/history (moved from refund code into `app/audit.py`, generalized with a `flag_id` target).
- Error classes and their 403/404/409/422 handler (moved to `app/errors.py`).
- Reason validation (moved to `app/validation.py`).
- Layout, banners, error page, `ts` timestamp filter, badge styles.
- Test fixtures: `app`, `conn`, `users`, `as_user`, `UserClient` (no changes needed for flags).

**New for Sandbox Flags:**
- `feature_flags` and `tool_permissions` tables, seeds (3 flags, Fiona + `flags.edit`), audit table migration.
- `app/identity.py` grant lookup (`has_permission`).
- `app/flags.py`: permission check, enable/disable, version-checked update + audit in one transaction.
- Three routes (`GET /flags`, `GET /flags/{id}`, `POST /flags/{id}`) and two templates.
- `tests/test_flags.py`, `tests/test_money_and_migration.py`.

**Shared changes made in the same pass:** "Internal Operations" navigation, status badges, readable timestamps, and dollar-amount refund input.

## Test results

`python -m pytest -q` → **84 passed** locally, in a clean clone, and in GitHub Actions (previously 35 for the refund-only version).

**Reproducibility check:** a fresh `git clone` of this branch into an empty directory, `python3 -m venv .venv`, `pip install -r requirements-dev.txt`, `python -m pytest -q` (84 passed), then `uvicorn` with `DEMO_MODE=true`, a generated `SESSION_SECRET` and a new `DATABASE_PATH`: `/login` 200, `/static/app.css` 200, anonymous `/requests` 303 to login, and without `DEMO_MODE` → 503. Checked on Ubuntu with Python 3.10.12 only.

| File | Covers |
|---|---|
| `test_permissions.py` | role rules, 503 without demo mode, client role/actor ignored, CSRF on every form, GET never mutates, 404s |
| `test_validation.py` | invalid dollar amounts and blank reasons rejected with no rows written; trimming; labels |
| `test_decisions_and_audit.py` | self-approval, sequential + threaded concurrent conflicts, audit consistency, audit-failure rollback, busy-timeout 503, DB constraints |
| `test_flags.py` | per-tool permission labels; stale-form 409 page links to the flag; everyone views; only Fiona edits (403 for others, 401 anonymous); Fiona cannot submit or decide refunds; reason required; CSRF; GET never mutates; stale form → 409; 404; audit-failure rollback; audit target CHECK |
| `test_money_and_migration.py` | exact dollar→cents conversion and rejects; refund-only audit table migrated with history preserved, idempotent |

**Browser check** (local Chromium against the running app): submitted `12.50` (stored 1250 cents) after `12.505` was rejected; Fiona saw no decision buttons and got 403 on the submit page; Rita approved and the queue showed "Approved — payment not executed."; Rita saw Sandbox Flags read-only; Fiona disabled a flag with a reason and the audit row appeared; a second Fiona tab with the older form got 409.

## Observed time

Source: the Devin session's event log timestamps (server-recorded, UTC); the VM clock agrees with them.

| Event | Time (EST) |
|---|---|
| Prototype start recorded | 17:45:01 |
| Refund prototype reported (PR #1 opened) | 17:50:07 |
| Extension requested | 17:59:16 |
| Extension cap (40 min) | 18:39:16 |

The extension and the final polish pass (requested 18:03 EST, capped at 20 minutes) each finished within their caps; exact finish times are in the session log and PR commit history. These are wall-clock timestamps from one session; they are not a measured estimate of engineering effort and should not be extrapolated to other applications.

## Known limitations

- Demo identity allows impersonation; there is no real authentication.
- Single SQLite file, single process; no multi-instance concurrency.
- Each additional audited resource type requires an audit-table schema change; see the tradeoff above.
- Flag routes live in `app/main.py` next to refund routes; a third tool would justify splitting routes per module.
- CI runs tests only (no lint, coverage or deployment). No pagination, no notifications, no editing/withdrawing requests, no flag creation/deletion UI.
- Sandbox Flags has no SDK, targeting or rollout percentages, and nothing reads the flags.

## Production responsibilities still owned by the company

- Real authentication (SSO), authorization model and access reviews.
- Hosting, network exposure, TLS, secret management (`SESSION_SECRET`), backups and disaster recovery.
- Database choice, migrations process, data retention, and tamper-evident audit storage.
- Payment/ledger integration and financial controls, if any are ever connected.
- Security review, compliance sign-off, monitoring, incident response and on-call.
- Code review and ownership of everything Devin produces; CI and release process.

## Simulated vs. out of scope

- **Simulated:** login (identity picker, impersonation allowed), users, requests and flags (synthetic), approval (status change only, no money moves), flags (stored values only, nothing reads them).
- **Out of scope:** production SSO/authentication, real payment/processor/ledger integrations, public deployment, multi-instance concurrency (single SQLite file), microservices, a generic workflow builder, feature-flag SDKs/targeting/rollouts, notifications, editing/withdrawing requests, pagination.
