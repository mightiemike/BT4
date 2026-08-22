# Q5579: src/mysql — query-cost DoS

## Question
Can an unprivileged attacker submit an id whose hex/base64 form aliases another row to `init` in `src/mysql.ts` such that init runs unbounded/expensive query on an id whose hex/base64 form aliases another row, breaking the invariant that query cost bounded and indexed, and leading to: dos of storage layer?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-mysql/src/mysql.ts` -> `init`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: an id whose hex/base64 form aliases another row
- Exploit idea: init runs unbounded/expensive query on an id whose hex/base64 form aliases another row
- Invariant to test: query cost bounded and indexed
- Expected Immunefi impact: DoS of storage layer (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: long-input timing test
