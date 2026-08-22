# Q1306: src/sqlite — sql injection via shop

## Question
Can an unprivileged attacker submit an id crafted to hit the wrong table/column mapping to `init` in `src/sqlite.ts` such that findSessionsByShop builds SQL from an id crafted to hit the wrong table/column mapping, breaking the invariant that shop filter is parameterized, and leading to: sqli / cross-tenant token theft?

## Target
- File/function: `packages/apps/session-storage/shopify-app-session-storage-sqlite/src/sqlite.ts` -> `init`
- Entrypoint: Any request that causes a session id/shop to reach the datastore
- Attacker controls: an id crafted to hit the wrong table/column mapping
- Exploit idea: findSessionsByShop builds SQL from an id crafted to hit the wrong table/column mapping
- Invariant to test: shop filter is parameterized
- Expected Immunefi impact: SQLi / cross-tenant token theft (In scope: SQL injection / cross-tenant session & token compromise. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: inject payload in shop filter
