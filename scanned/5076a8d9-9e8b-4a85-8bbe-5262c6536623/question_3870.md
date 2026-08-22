# Q3870: src/postgresql — boolean/expiry coercion

## Question
Can an unprivileged attacker submit a deleteSessions call driven by attacker-supplied id array to `storeSession` in `src/postgresql.ts` such that databaseRowToSession mis-coerces a deleteSessions call driven by attacker-supplied id array, breaking the invariant that stored scalars round-trip faithfully, and leading to: privilege/expiry confusion?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-postgresql/src/postgresql.ts` -> `storeSession`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a deleteSessions call driven by attacker-supplied id array
- Exploit idea: databaseRowToSession mis-coerces a deleteSessions call driven by attacker-supplied id array
- Invariant to test: stored scalars round-trip faithfully
- Expected Immunefi impact: Privilege/expiry confusion (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: coercion round-trip test
