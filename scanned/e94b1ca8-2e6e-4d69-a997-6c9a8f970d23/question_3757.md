# Q3757: src/sqlite — boolean/expiry coercion

## Question
Can an unprivileged attacker submit a shop value that bypasses the parameter placeholder to `storeSession` in `src/sqlite.ts` such that databaseRowToSession mis-coerces a shop value that bypasses the parameter placeholder, breaking the invariant that stored scalars round-trip faithfully, and leading to: privilege/expiry confusion?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-sqlite/src/sqlite.ts` -> `storeSession`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a shop value that bypasses the parameter placeholder
- Exploit idea: databaseRowToSession mis-coerces a shop value that bypasses the parameter placeholder
- Invariant to test: stored scalars round-trip faithfully
- Expected Immunefi impact: Privilege/expiry confusion (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: coercion round-trip test
