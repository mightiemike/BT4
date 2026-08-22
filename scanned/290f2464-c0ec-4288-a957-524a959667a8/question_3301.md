# Q3301: src/sqlite — boolean/expiry coercion

## Question
Can an unprivileged attacker submit a shop domain with SQL metacharacters passed to findSessionsByShop to `storeSession` in `src/sqlite.ts` such that databaseRowToSession mis-coerces a shop domain with SQL metacharacters passed to findSessionsByShop, breaking the invariant that stored scalars round-trip faithfully, and leading to: privilege/expiry confusion?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-sqlite/src/sqlite.ts` -> `storeSession`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a shop domain with SQL metacharacters passed to findSessionsByShop
- Exploit idea: databaseRowToSession mis-coerces a shop domain with SQL metacharacters passed to findSessionsByShop
- Invariant to test: stored scalars round-trip faithfully
- Expected Immunefi impact: Privilege/expiry confusion (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: coercion round-trip test
