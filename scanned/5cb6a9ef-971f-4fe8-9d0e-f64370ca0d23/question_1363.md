# Q1363: src/sqlite — sql injection via shop

## Question
Can an unprivileged attacker submit a shop value that bypasses the parameter placeholder to `databaseRowToSession` in `src/sqlite.ts` such that findSessionsByShop builds SQL from a shop value that bypasses the parameter placeholder, breaking the invariant that shop filter is parameterized, and leading to: sqli / cross-tenant token theft?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-sqlite/src/sqlite.ts` -> `databaseRowToSession`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a shop value that bypasses the parameter placeholder
- Exploit idea: findSessionsByShop builds SQL from a shop value that bypasses the parameter placeholder
- Invariant to test: shop filter is parameterized
- Expected Immunefi impact: SQLi / cross-tenant token theft (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: inject payload in shop filter
