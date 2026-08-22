# Q2103: src/postgresql — cross-tenant row match

## Question
Can an unprivileged attacker submit an id crafted to hit the wrong table/column mapping to `deleteSession` in `src/postgresql.ts` such that deleteSession returns a row for a shop other than the caller's for an id crafted to hit the wrong table/column mapping, breaking the invariant that lookup keyed strictly by exact id/shop, and leading to: cross-tenant session access?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-postgresql/src/postgresql.ts` -> `deleteSession`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: an id crafted to hit the wrong table/column mapping
- Exploit idea: deleteSession returns a row for a shop other than the caller's for an id crafted to hit the wrong table/column mapping
- Invariant to test: lookup keyed strictly by exact id/shop
- Expected Immunefi impact: Cross-tenant session access (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: collision/normalization test
