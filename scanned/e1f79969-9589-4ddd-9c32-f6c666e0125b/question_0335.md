# Q0335: src/mysql — sql injection via id

## Question
Can an unprivileged attacker submit concurrent storeSession calls racing the same id to `findSessionsByShop` in `src/mysql.ts` such that findSessionsByShop concatenates concurrent storeSession calls racing the same id into SQL instead of a bound parameter, breaking the invariant that all identifiers passed as bound params, and leading to: sqli -> read/overwrite other shops' tokens?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-mysql/src/mysql.ts` -> `findSessionsByShop`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: concurrent storeSession calls racing the same id
- Exploit idea: findSessionsByShop concatenates concurrent storeSession calls racing the same id into SQL instead of a bound parameter
- Invariant to test: all identifiers passed as bound params
- Expected Immunefi impact: SQLi -> read/overwrite other shops' tokens (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: inject quote in id, assert parameterized
