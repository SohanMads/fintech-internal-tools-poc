# Architecture

## Implemented in the prototype

| Concern | Prototype |
|---|---|
| Runtime | One local FastAPI process with Jinja2 templates |
| Database | One SQLite file, foreign keys on, `BEGIN IMMEDIATE` transactions |
| Identity | Demo identity picker behind `DEMO_MODE`, signed session cookie holding the user ID and CSRF token |
| Permissions | `users.role` for refunds, `tool_permissions` grants for flags, both loaded from the database on each request |
| Concurrency | Conditional `UPDATE` on refund status, version check on flags, 409 for the losing write |
| Audit | `audit_events` written in the same transaction as each change, one nullable foreign key per audited resource |
| Integrations | None. Approvals change a status only, and nothing reads the flags. |
| CI | GitHub Actions runs `python -m pytest -q` |

The module layout is in the [README](../README.md#how-it-works).

## Possible production changes

None of these exist in the prototype. Each one depends on the company's existing infrastructure and would need its own design and security review.

- **Identity:** replace the demo picker with the company's SSO (for example Entra ID over OIDC, since Power Apps tenants already use it). Map identity-provider groups to app permissions and remove access through deprovisioning.
- **Permissions:** merge roles and tool grants into one table of principal, app, and permission, so a new tool does not need a schema change.
- **Database:** move from SQLite to a managed database per environment. Add a migration process and backups.
- **Audit:** revoke UPDATE and DELETE on the audit table from the app's database role and export events to write-once storage. With more audited resource types, a `target_type`/`target_id` pair avoids a schema change per tool but moves target integrity checks into application code.
- **Integrations:** call existing service APIs, such as a payment processor's refund API with an idempotency key, instead of writing to other systems' databases.
- **Hosting and CI:** deploy behind the company's access controls, not the public internet. Add security scanning and required code review to CI.
