# Q3528: src/postgresql — boolean/expiry coercion

## Question
Can an unprivileged attacker submit concurrent storeSession calls racing the same id to `disconnect` in `src/postgresql.ts` such that databaseRowToSession mis-coerces concurrent storeSession calls racing the same id, breaking the invariant that stored scalars round-trip faithfully, and leading to: privilege/expiry confusion?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-postgresql/src/postgresql.ts` -> `disconnect`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: concurrent storeSession calls racing the same id
- Exploit idea: databaseRowToSession mis-coerces concurrent storeSession calls racing the same id
- Invariant to test: stored scalars round-trip faithfully
- Expected Immunefi impact: Privilege/expiry confusion (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: coercion round-trip test
