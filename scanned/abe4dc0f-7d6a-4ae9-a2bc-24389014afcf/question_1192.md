# Q1192: src/sqlite — sql injection via shop

## Question
Can an unprivileged attacker submit a boolean/expiry column coerced from attacker input to `deleteSessions` in `src/sqlite.ts` such that findSessionsByShop builds SQL from a boolean/expiry column coerced from attacker input, breaking the invariant that shop filter is parameterized, and leading to: sqli / cross-tenant token theft?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-sqlite/src/sqlite.ts` -> `deleteSessions`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a boolean/expiry column coerced from attacker input
- Exploit idea: findSessionsByShop builds SQL from a boolean/expiry column coerced from attacker input
- Invariant to test: shop filter is parameterized
- Expected Immunefi impact: SQLi / cross-tenant token theft (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: inject payload in shop filter
