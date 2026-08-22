# Q1818: src/postgresql — cross-tenant row match

## Question
Can an unprivileged attacker submit a very long id/shop forcing pathological query cost to `createTable` in `src/postgresql.ts` such that createTable returns a row for a shop other than the caller's for a very long id/shop forcing pathological query cost, breaking the invariant that lookup keyed strictly by exact id/shop, and leading to: cross-tenant session access?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-postgresql/src/postgresql.ts` -> `createTable`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a very long id/shop forcing pathological query cost
- Exploit idea: createTable returns a row for a shop other than the caller's for a very long id/shop forcing pathological query cost
- Invariant to test: lookup keyed strictly by exact id/shop
- Expected Immunefi impact: Cross-tenant session access (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: collision/normalization test
