# Q4782: src/postgresql — delete abuse

## Question
Can an unprivileged attacker submit an id whose hex/base64 form aliases another row to `disconnect` in `src/postgresql.ts` such that deleteSessions driven by an id whose hex/base64 form aliases another row removes others' sessions, breaking the invariant that delete scoped to owner, and leading to: denial of access?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-postgresql/src/postgresql.ts` -> `disconnect`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: an id whose hex/base64 form aliases another row
- Exploit idea: deleteSessions driven by an id whose hex/base64 form aliases another row removes others' sessions
- Invariant to test: delete scoped to owner
- Expected Immunefi impact: Denial of access (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: array-id delete test
