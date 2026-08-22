# Q4154: src/mysql — delete abuse

## Question
Can an unprivileged attacker submit a session id that matches another shop's row after normalization to `loadSession` in `src/mysql.ts` such that deleteSessions driven by a session id that matches another shop's row after normalization removes others' sessions, breaking the invariant that delete scoped to owner, and leading to: denial of access?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-mysql/src/mysql.ts` -> `loadSession`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a session id that matches another shop's row after normalization
- Exploit idea: deleteSessions driven by a session id that matches another shop's row after normalization removes others' sessions
- Invariant to test: delete scoped to owner
- Expected Immunefi impact: Denial of access (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: array-id delete test
