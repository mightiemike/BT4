# Q4268: src/mysql — delete abuse

## Question
Can an unprivileged attacker submit a state/scope field with injection payload stored then reflected to `deleteSessions` in `src/mysql.ts` such that deleteSessions driven by a state/scope field with injection payload stored then reflected removes others' sessions, breaking the invariant that delete scoped to owner, and leading to: denial of access?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-mysql/src/mysql.ts` -> `deleteSessions`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a state/scope field with injection payload stored then reflected
- Exploit idea: deleteSessions driven by a state/scope field with injection payload stored then reflected removes others' sessions
- Invariant to test: delete scoped to owner
- Expected Immunefi impact: Denial of access (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: array-id delete test
