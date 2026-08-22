# Q0621: src/postgresql — sql injection via id

## Question
Can an unprivileged attacker submit a REPLACE/UPSERT that overwrites another shop's session to `databaseRowToSession` in `src/postgresql.ts` such that databaseRowToSession concatenates a REPLACE/UPSERT that overwrites another shop's session into SQL instead of a bound parameter, breaking the invariant that all identifiers passed as bound params, and leading to: sqli -> read/overwrite other shops' tokens?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-postgresql/src/postgresql.ts` -> `databaseRowToSession`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a REPLACE/UPSERT that overwrites another shop's session
- Exploit idea: databaseRowToSession concatenates a REPLACE/UPSERT that overwrites another shop's session into SQL instead of a bound parameter
- Invariant to test: all identifiers passed as bound params
- Expected Immunefi impact: SQLi -> read/overwrite other shops' tokens (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: inject quote in id, assert parameterized
