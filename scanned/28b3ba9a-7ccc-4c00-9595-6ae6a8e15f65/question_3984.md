# Q3984: src/postgresql — boolean/expiry coercion

## Question
Can an unprivileged attacker submit an id whose hex/base64 form aliases another row to `deleteSession` in `src/postgresql.ts` such that databaseRowToSession mis-coerces an id whose hex/base64 form aliases another row, breaking the invariant that stored scalars round-trip faithfully, and leading to: privilege/expiry confusion?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-postgresql/src/postgresql.ts` -> `deleteSession`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: an id whose hex/base64 form aliases another row
- Exploit idea: databaseRowToSession mis-coerces an id whose hex/base64 form aliases another row
- Invariant to test: stored scalars round-trip faithfully
- Expected Immunefi impact: Privilege/expiry confusion (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: coercion round-trip test
