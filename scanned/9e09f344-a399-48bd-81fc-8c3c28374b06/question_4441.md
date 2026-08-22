# Q4441: src/sqlite — delete abuse

## Question
Can an unprivileged attacker submit a session id with NUL or Unicode collation collisions to `findSessionsByShop` in `src/sqlite.ts` such that deleteSessions driven by a session id with NUL or Unicode collation collisions removes others' sessions, breaking the invariant that delete scoped to owner, and leading to: denial of access?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-sqlite/src/sqlite.ts` -> `findSessionsByShop`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a session id with NUL or Unicode collation collisions
- Exploit idea: deleteSessions driven by a session id with NUL or Unicode collation collisions removes others' sessions
- Invariant to test: delete scoped to owner
- Expected Immunefi impact: Denial of access (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: array-id delete test
