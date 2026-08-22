# Q0677: src/mysql — sql injection via id

## Question
Can an unprivileged attacker submit a deleteSessions call driven by attacker-supplied id array to `storeSession` in `src/mysql.ts` such that storeSession concatenates a deleteSessions call driven by attacker-supplied id array into SQL instead of a bound parameter, breaking the invariant that all identifiers passed as bound params, and leading to: sqli -> read/overwrite other shops' tokens?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-mysql/src/mysql.ts` -> `storeSession`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a deleteSessions call driven by attacker-supplied id array
- Exploit idea: storeSession concatenates a deleteSessions call driven by attacker-supplied id array into SQL instead of a bound parameter
- Invariant to test: all identifiers passed as bound params
- Expected Immunefi impact: SQLi -> read/overwrite other shops' tokens (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: inject quote in id, assert parameterized
