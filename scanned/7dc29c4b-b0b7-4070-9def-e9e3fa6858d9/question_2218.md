# Q2218: src/sqlite — cross-tenant row match

## Question
Can an unprivileged attacker submit a REPLACE/UPSERT that overwrites another shop's session to `init` in `src/sqlite.ts` such that init returns a row for a shop other than the caller's for a REPLACE/UPSERT that overwrites another shop's session, breaking the invariant that lookup keyed strictly by exact id/shop, and leading to: cross-tenant session access?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-sqlite/src/sqlite.ts` -> `init`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a REPLACE/UPSERT that overwrites another shop's session
- Exploit idea: init returns a row for a shop other than the caller's for a REPLACE/UPSERT that overwrites another shop's session
- Invariant to test: lookup keyed strictly by exact id/shop
- Expected Immunefi impact: Cross-tenant session access (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: collision/normalization test
