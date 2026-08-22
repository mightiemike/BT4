# Q2160: src/postgresql — cross-tenant row match

## Question
Can an unprivileged attacker submit a shop value that bypasses the parameter placeholder to `deleteSessions` in `src/postgresql.ts` such that deleteSessions returns a row for a shop other than the caller's for a shop value that bypasses the parameter placeholder, breaking the invariant that lookup keyed strictly by exact id/shop, and leading to: cross-tenant session access?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-postgresql/src/postgresql.ts` -> `deleteSessions`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a shop value that bypasses the parameter placeholder
- Exploit idea: deleteSessions returns a row for a shop other than the caller's for a shop value that bypasses the parameter placeholder
- Invariant to test: lookup keyed strictly by exact id/shop
- Expected Immunefi impact: Cross-tenant session access (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: collision/normalization test
