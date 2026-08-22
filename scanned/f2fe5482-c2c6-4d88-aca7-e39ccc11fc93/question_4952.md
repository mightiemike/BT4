# Q4952: src/mysql — query-cost DoS

## Question
Can an unprivileged attacker submit a session id that matches another shop's row after normalization to `disconnect` in `src/mysql.ts` such that disconnect runs unbounded/expensive query on a session id that matches another shop's row after normalization, breaking the invariant that query cost bounded and indexed, and leading to: dos of storage layer?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-mysql/src/mysql.ts` -> `disconnect`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a session id that matches another shop's row after normalization
- Exploit idea: disconnect runs unbounded/expensive query on a session id that matches another shop's row after normalization
- Invariant to test: query cost bounded and indexed
- Expected Immunefi impact: DoS of storage layer (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: long-input timing test
