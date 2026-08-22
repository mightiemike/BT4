# Q1874: src/mysql — cross-tenant row match

## Question
Can an unprivileged attacker submit a state/scope field with injection payload stored then reflected to `loadSession` in `src/mysql.ts` such that loadSession returns a row for a shop other than the caller's for a state/scope field with injection payload stored then reflected, breaking the invariant that lookup keyed strictly by exact id/shop, and leading to: cross-tenant session access?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-mysql/src/mysql.ts` -> `loadSession`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a state/scope field with injection payload stored then reflected
- Exploit idea: loadSession returns a row for a shop other than the caller's for a state/scope field with injection payload stored then reflected
- Invariant to test: lookup keyed strictly by exact id/shop
- Expected Immunefi impact: Cross-tenant session access (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: collision/normalization test
