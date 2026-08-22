# Q5410: src/sqlite — query-cost DoS

## Question
Can an unprivileged attacker submit a REPLACE/UPSERT that overwrites another shop's session to `init` in `src/sqlite.ts` such that init runs unbounded/expensive query on a REPLACE/UPSERT that overwrites another shop's session, breaking the invariant that query cost bounded and indexed, and leading to: dos of storage layer?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-sqlite/src/sqlite.ts` -> `init`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a REPLACE/UPSERT that overwrites another shop's session
- Exploit idea: init runs unbounded/expensive query on a REPLACE/UPSERT that overwrites another shop's session
- Invariant to test: query cost bounded and indexed
- Expected Immunefi impact: DoS of storage layer (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: long-input timing test
