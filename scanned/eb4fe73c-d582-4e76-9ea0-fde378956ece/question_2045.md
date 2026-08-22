# Q2045: src/mysql — cross-tenant row match

## Question
Can an unprivileged attacker submit a session id with NUL or Unicode collation collisions to `findSessionsByShop` in `src/mysql.ts` such that findSessionsByShop returns a row for a shop other than the caller's for a session id with NUL or Unicode collation collisions, breaking the invariant that lookup keyed strictly by exact id/shop, and leading to: cross-tenant session access?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-mysql/src/mysql.ts` -> `findSessionsByShop`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a session id with NUL or Unicode collation collisions
- Exploit idea: findSessionsByShop returns a row for a shop other than the caller's for a session id with NUL or Unicode collation collisions
- Invariant to test: lookup keyed strictly by exact id/shop
- Expected Immunefi impact: Cross-tenant session access (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: collision/normalization test
