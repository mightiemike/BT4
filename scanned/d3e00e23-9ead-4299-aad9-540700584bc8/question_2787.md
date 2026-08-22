# Q2787: src/postgresql — upsert overwrite

## Question
Can an unprivileged attacker submit a boolean/expiry column coerced from attacker input to `deleteSessions` in `src/postgresql.ts` such that storeSession REPLACE/UPSERT lets a boolean/expiry column coerced from attacker input overwrite another id, breaking the invariant that writes touch only the caller's own id, and leading to: session hijack via overwrite?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-postgresql/src/postgresql.ts` -> `deleteSessions`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a boolean/expiry column coerced from attacker input
- Exploit idea: storeSession REPLACE/UPSERT lets a boolean/expiry column coerced from attacker input overwrite another id
- Invariant to test: writes touch only the caller's own id
- Expected Immunefi impact: Session hijack via overwrite (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: overwrite test
