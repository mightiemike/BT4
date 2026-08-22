# Q5124: src/postgresql — query-cost DoS

## Question
Can an unprivileged attacker submit concurrent storeSession calls racing the same id to `storeSession` in `src/postgresql.ts` such that storeSession runs unbounded/expensive query on concurrent storeSession calls racing the same id, breaking the invariant that query cost bounded and indexed, and leading to: dos of storage layer?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-postgresql/src/postgresql.ts` -> `storeSession`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: concurrent storeSession calls racing the same id
- Exploit idea: storeSession runs unbounded/expensive query on concurrent storeSession calls racing the same id
- Invariant to test: query cost bounded and indexed
- Expected Immunefi impact: DoS of storage layer (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: long-input timing test
