# How this was built with Devin

The whole project was built in one Devin session. Requirements came in as chat messages. Changes went through GitHub pull requests.

## Requests and results

| # | Request | Result |
|---|---|---|
| 1 | Create the repo, connect it to Devin, start a session, record the start time. | The repo-creation card did not appear for the user, so the user created the repo by hand. |
| 2 | Confirm repo access before writing code. | Clone and a dry-run push both worked. |
| 3 | Do minimal environment setup and propose a plan for the refund tool. Wait for review. | Plan with schema, test list, and time allocation. |
| 4 | Approve the plan with corrections: `DEMO_MODE` must fail closed, server-side identity and roles only, CSRF on every form including identity switching, no GET mutations, SQLite foreign keys on, trimmed reasons, consistent status fields, 404/403/409 codes, bounded busy timeout, accurate "first committed decision" and "insert-only" wording. | [PR #1](https://github.com/SohanMads/fintech-internal-tools-poc/pull/1) opened with the refund workflow and 35 tests. |
| 5 | Add a Sandbox Flags tool on the shared foundation, "Internal Operations" navigation, dollar amount input, and README sections on reuse and limitations. | Flags tool, audit table migration, shared modules for audit and errors. 82 tests. |
| 6 | Final pass: per-tool permission labels, stale-form and error-page navigation, GitHub Actions CI, a clean-clone reproducibility check, accurate audit wording, UI polish. | Labels like "Refunds: viewer · Flags: admin". Stale flag 409 links to the flag. CI workflow passing. Shared stylesheet. 84 tests. |
| 7 | Show the README. | README attached. |
| 8 | Finalize for submission: repo description, README title, conflict demo with a fresh request, mobile-table limitation, secrets check, merge PR #1. | README fixed and PR #1 merged. The repo description could not be set from the session. |
| 9 | Rewrite the README for a reviewer and move process detail to build notes. | [PR #2](https://github.com/SohanMads/fintech-internal-tools-poc/pull/2) with a shorter README and `docs/build-notes.md`. |
| 10 | Three README wording fixes, merge PR #2, then review the code. | PR #2 merged. Code review returned in chat. No code changes. |
| 11 | This documentation pass: Power Apps comparison, screenshots, target architecture, this file. | Documentation-only PR. |

## What Devin got right

- The first plan already had atomic audit writes in the same transaction and a conditional update for competing reviewers.
- Tests cover the hard cases: concurrent decisions from two threads, audit-failure rollback, and forged role or actor fields.
- The audit table migration kept existing history and verified row counts and foreign keys before committing.
- Adding flags did not touch refund rules. Shared code moved into its own modules.

## What needed human review

- The user's approval of the first plan added requirements the plan did not spell out: fail-closed demo mode, CSRF on every form including identity switching, foreign keys on every connection, and exact error codes.
- The header showed Fiona as "Flag Admin (viewer)" while she edited flags. The user asked for per-tool labels.
- A stale flag form returned to the refund queue instead of the flag. The user caught it.
- CI was not added until the user asked.
- The README's conflict demo used a request that was already decided. The user caught it.
- The README read like a completion report. The user asked for a rewrite.
- The approved status badge on the refund detail page overlaps the requester column at desktop widths. This was found while taking screenshots and is not fixed.
- The session had no GitHub API access. It could not set the repo description or see `main` CI runs, and it merged PRs by pushing merge commits to `main`.

## Draft Playbook: Add internal tool #N on the shared foundation

Paste this into Devin as a Playbook.

```markdown
# Add internal tool #N on the shared foundation

## Inputs the requester must provide
- Workflow: what a user does, step by step, and what states a record moves through.
- Roles: who can view, create, change, and approve. Note any no-self-approval rule.
- Data: fields, types, validation rules, and sample synthetic records.
- Audit events: which actions must be recorded and what each record must contain.
- Integrations: any external API the tool calls. Say "none" if there are none.

## Steps
1. Read README.md, docs/architecture.md, and app/ to learn the shared modules. Do not change refund or flag rules.
2. Schema and migration: add the tool's tables in app/db.py. Add the audit target column and CHECK changes. Write a migration that keeps existing audit rows and verifies row count and foreign keys.
3. Rules module: create app/<tool>.py. Put every permission check, validation rule, and state change here. Wrap each change and its audit.record call in one db.transaction.
4. Concurrency: use a conditional UPDATE on status or a version column. Return 409 when no row changes.
5. Routes: add GET pages and POST actions in app/main.py. Every POST uses checked_form. No GET request changes data.
6. Templates: extend base.html. Reuse error.html and _audit_table.html. Add a nav link.
7. Permission grants: add the new permissions to tool_permissions and seed demo users that hold them.
8. Tests in tests/test_<tool>.py:
   - Permission tests for every role, including forbidden actions (403).
   - CSRF required on every POST, and GET never changes data.
   - Concurrency or stale-form test that proves only one write wins (409 for the other).
   - Audit-rollback test that forces audit.record to fail and checks nothing was saved.
   - Validation tests for every input rule.
9. Run python -m pytest -q. All tests must pass.
10. Browser check: run with DEMO_MODE=true, walk through the workflow as each role, and take screenshots.
11. Update README.md with the new tool and open a PR.

## Definition of done
- All new and existing tests pass locally and in CI.
- Every state change writes an audit event in the same transaction.
- Permissions are enforced on the server and covered by tests.
- Screenshots of the main pages are in the PR.
- The PR description lists what was reused and what was new.
- No changes to existing tools' rules.
```
