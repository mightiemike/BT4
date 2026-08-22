# Q1019: src/mysql — sql injection via shop

## Question
Can an unprivileged attacker submit a very long id/shop forcing pathological query cost to `init` in `src/mysql.ts` such that findSessionsByShop builds SQL from a very long id/shop forcing pathological query cost, breaking the invariant that shop filter is parameterized, and leading to: sqli / cross-tenant token theft?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-mysql/src/mysql.ts` -> `init`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a very long id/shop forcing pathological query cost
- Exploit idea: findSessionsByShop builds SQL from a very long id/shop forcing pathological query cost
- Invariant to test: shop filter is parameterized
- Expected Immunefi impact: SQLi / cross-tenant token theft (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: inject payload in shop filter
