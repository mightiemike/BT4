# Q1933: src/sqlite — cross-tenant row match

## Question
Can an unprivileged attacker submit concurrent storeSession calls racing the same id to `storeSession` in `src/sqlite.ts` such that storeSession returns a row for a shop other than the caller's for concurrent storeSession calls racing the same id, breaking the invariant that lookup keyed strictly by exact id/shop, and leading to: cross-tenant session access?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-sqlite/src/sqlite.ts` -> `storeSession`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: concurrent storeSession calls racing the same id
- Exploit idea: storeSession returns a row for a shop other than the caller's for concurrent storeSession calls racing the same id
- Invariant to test: lookup keyed strictly by exact id/shop
- Expected Immunefi impact: Cross-tenant session access (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: collision/normalization test
