# Q3015: src/postgresql — upsert overwrite

## Question
Can an unprivileged attacker submit a REPLACE/UPSERT that overwrites another shop's session to `connectClient` in `src/postgresql.ts` such that storeSession REPLACE/UPSERT lets a REPLACE/UPSERT that overwrites another shop's session overwrite another id, breaking the invariant that writes touch only the caller's own id, and leading to: session hijack via overwrite?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-postgresql/src/postgresql.ts` -> `connectClient`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a REPLACE/UPSERT that overwrites another shop's session
- Exploit idea: storeSession REPLACE/UPSERT lets a REPLACE/UPSERT that overwrites another shop's session overwrite another id
- Invariant to test: writes touch only the caller's own id
- Expected Immunefi impact: Session hijack via overwrite (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: overwrite test
