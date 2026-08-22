# Q3584: src/mysql — boolean/expiry coercion

## Question
Can an unprivileged attacker submit a boolean/expiry column coerced from attacker input to `loadSession` in `src/mysql.ts` such that databaseRowToSession mis-coerces a boolean/expiry column coerced from attacker input, breaking the invariant that stored scalars round-trip faithfully, and leading to: privilege/expiry confusion?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-mysql/src/mysql.ts` -> `loadSession`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a boolean/expiry column coerced from attacker input
- Exploit idea: databaseRowToSession mis-coerces a boolean/expiry column coerced from attacker input
- Invariant to test: stored scalars round-trip faithfully
- Expected Immunefi impact: Privilege/expiry confusion (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: coercion round-trip test
