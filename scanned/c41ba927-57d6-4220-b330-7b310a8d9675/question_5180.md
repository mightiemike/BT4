# Q5180: src/mysql — query-cost DoS

## Question
Can an unprivileged attacker submit a boolean/expiry column coerced from attacker input to `MySQLSessionStorage` in `src/mysql.ts` such that MySQLSessionStorage runs unbounded/expensive query on a boolean/expiry column coerced from attacker input, breaking the invariant that query cost bounded and indexed, and leading to: dos of storage layer?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-mysql/src/mysql.ts` -> `MySQLSessionStorage`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a boolean/expiry column coerced from attacker input
- Exploit idea: MySQLSessionStorage runs unbounded/expensive query on a boolean/expiry column coerced from attacker input
- Invariant to test: query cost bounded and indexed
- Expected Immunefi impact: DoS of storage layer (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: long-input timing test
