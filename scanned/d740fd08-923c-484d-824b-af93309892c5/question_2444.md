# Q2444: src/mysql — upsert overwrite

## Question
Can an unprivileged attacker submit a session id containing a single quote or SQL metacharacter to `loadSession` in `src/mysql.ts` such that storeSession REPLACE/UPSERT lets a session id containing a single quote or SQL metacharacter overwrite another id, breaking the invariant that writes touch only the caller's own id, and leading to: session hijack via overwrite?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-mysql/src/mysql.ts` -> `loadSession`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a session id containing a single quote or SQL metacharacter
- Exploit idea: storeSession REPLACE/UPSERT lets a session id containing a single quote or SQL metacharacter overwrite another id
- Invariant to test: writes touch only the caller's own id
- Expected Immunefi impact: Session hijack via overwrite (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: overwrite test
