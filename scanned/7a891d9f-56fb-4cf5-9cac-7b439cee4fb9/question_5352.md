# Q5352: src/postgresql — query-cost DoS

## Question
Can an unprivileged attacker submit a shop value that bypasses the parameter placeholder to `findSessionsByShop` in `src/postgresql.ts` such that findSessionsByShop runs unbounded/expensive query on a shop value that bypasses the parameter placeholder, breaking the invariant that query cost bounded and indexed, and leading to: dos of storage layer?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-postgresql/src/postgresql.ts` -> `findSessionsByShop`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a shop value that bypasses the parameter placeholder
- Exploit idea: findSessionsByShop runs unbounded/expensive query on a shop value that bypasses the parameter placeholder
- Invariant to test: query cost bounded and indexed
- Expected Immunefi impact: DoS of storage layer (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: long-input timing test
