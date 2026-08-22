# Q1248: src/postgresql — sql injection via shop

## Question
Can an unprivileged attacker submit a session id with NUL or Unicode collation collisions to `databaseRowToSession` in `src/postgresql.ts` such that findSessionsByShop builds SQL from a session id with NUL or Unicode collation collisions, breaking the invariant that shop filter is parameterized, and leading to: sqli / cross-tenant token theft?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-postgresql/src/postgresql.ts` -> `databaseRowToSession`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a session id with NUL or Unicode collation collisions
- Exploit idea: findSessionsByShop builds SQL from a session id with NUL or Unicode collation collisions
- Invariant to test: shop filter is parameterized
- Expected Immunefi impact: SQLi / cross-tenant token theft (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: inject payload in shop filter
