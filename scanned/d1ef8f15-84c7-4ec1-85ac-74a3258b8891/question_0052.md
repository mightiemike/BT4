# Q0052: src/sqlite — sql injection via id

## Question
Can an unprivileged attacker submit a session id containing a single quote or SQL metacharacter to `SQLiteSessionStorage` in `src/sqlite.ts` such that SQLiteSessionStorage concatenates a session id containing a single quote or SQL metacharacter into SQL instead of a bound parameter, breaking the invariant that all identifiers passed as bound params, and leading to: sqli -> read/overwrite other shops' tokens?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-sqlite/src/sqlite.ts` -> `SQLiteSessionStorage`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a session id containing a single quote or SQL metacharacter
- Exploit idea: SQLiteSessionStorage concatenates a session id containing a single quote or SQL metacharacter into SQL instead of a bound parameter
- Invariant to test: all identifiers passed as bound params
- Expected Immunefi impact: SQLi -> read/overwrite other shops' tokens (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: inject quote in id, assert parameterized
