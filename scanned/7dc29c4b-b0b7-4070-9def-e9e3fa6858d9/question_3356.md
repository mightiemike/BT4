# Q3356: src/mysql — boolean/expiry coercion

## Question
Can an unprivileged attacker submit a session id that matches another shop's row after normalization to `createTable` in `src/mysql.ts` such that databaseRowToSession mis-coerces a session id that matches another shop's row after normalization, breaking the invariant that stored scalars round-trip faithfully, and leading to: privilege/expiry confusion?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-mysql/src/mysql.ts` -> `createTable`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a session id that matches another shop's row after normalization
- Exploit idea: databaseRowToSession mis-coerces a session id that matches another shop's row after normalization
- Invariant to test: stored scalars round-trip faithfully
- Expected Immunefi impact: Privilege/expiry confusion (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: coercion round-trip test
