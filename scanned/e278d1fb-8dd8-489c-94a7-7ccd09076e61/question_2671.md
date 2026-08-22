# Q2671: utils/processed-query — header canonicalization gap

## Question
Can an unprivileged attacker submit a query string with duplicated/array params to this module in `utils/processed-query.ts` such that canonicalizeHeaders/<module> mis-normalizes a query string with duplicated/array params, breaking the invariant that header lookup is case/format stable, and leading to: auth header confusion?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/processed-query.ts` -> (module scope)
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a query string with duplicated/array params
- Exploit idea: canonicalizeHeaders/<module> mis-normalizes a query string with duplicated/array params
- Invariant to test: header lookup is case/format stable
- Expected Immunefi impact: Auth header confusion (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: mixed-case header test
