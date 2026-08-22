# Q1476: src/postgresql — sql injection via shop

## Question
Can an unprivileged attacker submit a deleteSessions call driven by attacker-supplied id array to `deleteSession` in `src/postgresql.ts` such that findSessionsByShop builds SQL from a deleteSessions call driven by attacker-supplied id array, breaking the invariant that shop filter is parameterized, and leading to: sqli / cross-tenant token theft?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-postgresql/src/postgresql.ts` -> `deleteSession`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a deleteSessions call driven by attacker-supplied id array
- Exploit idea: findSessionsByShop builds SQL from a deleteSessions call driven by attacker-supplied id array
- Invariant to test: shop filter is parameterized
- Expected Immunefi impact: SQLi / cross-tenant token theft (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: inject payload in shop filter
