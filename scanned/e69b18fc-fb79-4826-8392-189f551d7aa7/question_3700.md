# Q3700: src/sqlite — boolean/expiry coercion

## Question
Can an unprivileged attacker submit an id crafted to hit the wrong table/column mapping to `SQLiteSessionStorage` in `src/sqlite.ts` such that databaseRowToSession mis-coerces an id crafted to hit the wrong table/column mapping, breaking the invariant that stored scalars round-trip faithfully, and leading to: privilege/expiry confusion?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-sqlite/src/sqlite.ts` -> `SQLiteSessionStorage`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: an id crafted to hit the wrong table/column mapping
- Exploit idea: databaseRowToSession mis-coerces an id crafted to hit the wrong table/column mapping
- Invariant to test: stored scalars round-trip faithfully
- Expected Immunefi impact: Privilege/expiry confusion (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: coercion round-trip test
