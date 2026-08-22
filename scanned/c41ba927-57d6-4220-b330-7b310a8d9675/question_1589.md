# Q1589: src/mysql — sql injection via shop

## Question
Can an unprivileged attacker submit an id whose hex/base64 form aliases another row to `init` in `src/mysql.ts` such that findSessionsByShop builds SQL from an id whose hex/base64 form aliases another row, breaking the invariant that shop filter is parameterized, and leading to: sqli / cross-tenant token theft?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-mysql/src/mysql.ts` -> `init`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: an id whose hex/base64 form aliases another row
- Exploit idea: findSessionsByShop builds SQL from an id whose hex/base64 form aliases another row
- Invariant to test: shop filter is parameterized
- Expected Immunefi impact: SQLi / cross-tenant token theft (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: inject payload in shop filter
