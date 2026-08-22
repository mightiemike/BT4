# Q4497: src/postgresql — delete abuse

## Question
Can an unprivileged attacker submit an id crafted to hit the wrong table/column mapping to `storeSession` in `src/postgresql.ts` such that deleteSessions driven by an id crafted to hit the wrong table/column mapping removes others' sessions, breaking the invariant that delete scoped to owner, and leading to: denial of access?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-postgresql/src/postgresql.ts` -> `storeSession`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: an id crafted to hit the wrong table/column mapping
- Exploit idea: deleteSessions driven by an id crafted to hit the wrong table/column mapping removes others' sessions
- Invariant to test: delete scoped to owner
- Expected Immunefi impact: Denial of access (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: array-id delete test
