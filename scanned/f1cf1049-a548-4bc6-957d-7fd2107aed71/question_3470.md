# Q3470: src/mysql — boolean/expiry coercion

## Question
Can an unprivileged attacker submit a state/scope field with injection payload stored then reflected to `MySQLSessionStorage` in `src/mysql.ts` such that databaseRowToSession mis-coerces a state/scope field with injection payload stored then reflected, breaking the invariant that stored scalars round-trip faithfully, and leading to: privilege/expiry confusion?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-mysql/src/mysql.ts` -> `MySQLSessionStorage`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a state/scope field with injection payload stored then reflected
- Exploit idea: databaseRowToSession mis-coerces a state/scope field with injection payload stored then reflected
- Invariant to test: stored scalars round-trip faithfully
- Expected Immunefi impact: Privilege/expiry confusion (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: coercion round-trip test
