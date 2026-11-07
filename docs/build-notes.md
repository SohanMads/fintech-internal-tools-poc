# Build notes

## Timeline and effort

- Devin build time: 23 minutes from start to the last application code push, across 3 review rounds.
- Human time reviewing and directing: ~__ minutes

These numbers are from one session and are not an estimate for other apps.

## Tests

`python -m pytest -q` runs 84 tests. Each test uses a temporary SQLite database.

| File | Covers |
|---|---|
| `test_permissions.py` | Role rules, 503 without demo mode, ignored client role and actor, CSRF on every form, GET never mutates, 404s |
| `test_validation.py` | Invalid amounts and blank reasons rejected with no rows written, trimming, status labels |
| `test_decisions_and_audit.py` | Self-approval, sequential and threaded concurrent conflicts, audit consistency, audit-failure rollback, busy-timeout 503, database constraints |
| `test_flags.py` | Permission labels, everyone views, only Fiona edits, Fiona cannot submit or decide refunds, reason required, CSRF, stale form 409, audit-failure rollback, audit target CHECK |
| `test_money_and_migration.py` | Exact dollar to cents conversion, rejected formats, refund-only audit table migrated with history preserved |
