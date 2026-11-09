# How this was built with Devin

The project was built in one Devin session. Requirements arrived as chat messages, and every change went through a GitHub pull request with the `pytest` check.

## Scope

A refund approval workflow with synthetic data, simulated sign-in, server-side permissions, no self-approval, safe concurrent decisions, and an audit trail. A small Sandbox Flags tool was then added on the same foundation to see what could be reused. Payments, real authentication, deployment, and a generic form builder were out of scope.

## Pull requests

| PR | Change |
|---|---|
| [#1](https://github.com/SohanMads/fintech-internal-tools-poc/pull/1) | Refund workflow (35 tests), then Sandbox Flags, shared audit and error modules, CI, and UI polish (84 tests) |
| [#2](https://github.com/SohanMads/fintech-internal-tools-poc/pull/2) | README rewrite |
| [#3](https://github.com/SohanMads/fintech-internal-tools-poc/pull/3) | Power Apps comparison, screenshots, architecture notes, this file |
| [#4](https://github.com/SohanMads/fintech-internal-tools-poc/pull/4) | CSS fix for the status badge overlapping the requester column |
| [#5](https://github.com/SohanMads/fintech-internal-tools-poc/pull/5) | Shorter UI copy and seed data, shorter docs |
| [#6](https://github.com/SohanMads/fintech-internal-tools-poc/pull/6) | Reject button label, unused constant removed |
| [#7](https://github.com/SohanMads/fintech-internal-tools-poc/pull/7) | Two edge-case fixes, docs consolidated |

## Time

- Devin build time: 23 minutes from start to the last application code push of PR #1, across 3 review rounds. Later fixes in PRs #4 to #7 are not included.

This number is from one session and is not an estimate for other apps.

## Corrections

Each of these was raised in the session and fixed in a later commit or PR.

- Approving the first plan added requirements it did not spell out: fail-closed demo mode, CSRF on every form including identity switching, foreign keys on every connection, and exact error codes.
- The header showed the flag admin's refund role only. The user asked for per-tool permission labels.
- A stale flag form linked back to the refund queue instead of the flag. The user caught it.
- CI was added only after the user asked for it.
- The README's conflict demo used a request that was already decided. The user caught it.
- The approved status badge overlapped the requester column. It was found in the documentation screenshots and fixed in PR #4.
- The session first reported the `main` CI runs as unverified. A later check found they had passed.
- Devin Review flagged that the draft Playbook left out the `tool_permissions` CHECK migration. The Playbook was corrected.
- The user reported two edge cases after merge. A stale flag form with an invalid reason was re-rendered with the newer version and the opposite action. A non-ASCII CSRF token caused a 500. Both are fixed with regression tests in PR #7.

## Playbook: add internal tool #N

1. Get the workflow, roles, fields, audit events, and integrations from the requester.
2. Add the tool's tables, the audit target column, and the new `tool_permissions` values in one migration that keeps existing audit rows.
3. Put permission checks, validation, and state changes in `app/<tool>.py`. Wrap each change and its `audit.record` call in one `db.transaction`.
4. Use a conditional `UPDATE` on status or version and return 409 when no row changes.
5. Add routes in `app/main.py` with `checked_form` on every POST, and templates that extend `base.html`.
6. Add tests for permissions, CSRF, stale writes, audit rollback, and validation. Run `python -m pytest -q`.
7. Check the workflow in a browser as each role, update the README, and open a PR.
