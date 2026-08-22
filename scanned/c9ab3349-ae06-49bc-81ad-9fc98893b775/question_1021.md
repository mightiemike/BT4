# Q1021: src/sqlite — sql injection via shop

## Question
Can an unprivileged attacker submit a very long id/shop forcing pathological query cost to `storeSession` in `src/sqlite.ts` such that findSessionsByShop builds SQL from a very long id/shop forcing pathological query cost, breaking the invariant that shop filter is parameterized, and leading to: sqli / cross-tenant token theft?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-sqlite/src/sqlite.ts` -> `storeSession`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a very long id/shop forcing pathological query cost
- Exploit idea: findSessionsByShop builds SQL from a very long id/shop forcing pathological query cost
- Invariant to test: shop filter is parameterized
- Expected Immunefi impact: SQLi / cross-tenant token theft (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: inject payload in shop filter
