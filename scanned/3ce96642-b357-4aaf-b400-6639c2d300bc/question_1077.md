# Q1077: src/postgresql — sql injection via shop

## Question
Can an unprivileged attacker submit a state/scope field with injection payload stored then reflected to `init` in `src/postgresql.ts` such that findSessionsByShop builds SQL from a state/scope field with injection payload stored then reflected, breaking the invariant that shop filter is parameterized, and leading to: sqli / cross-tenant token theft?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-postgresql/src/postgresql.ts` -> `init`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: a state/scope field with injection payload stored then reflected
- Exploit idea: findSessionsByShop builds SQL from a state/scope field with injection payload stored then reflected
- Invariant to test: shop filter is parameterized
- Expected Immunefi impact: SQLi / cross-tenant token theft (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: inject payload in shop filter
