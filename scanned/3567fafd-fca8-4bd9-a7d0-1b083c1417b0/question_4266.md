# Q4266: http/headers — query param collision

## Question
Can an unprivileged attacker submit a query string with duplicated/array params to `getHeader` in `http/headers.ts` such that processed-query/getHeader collapses a query string with duplicated/array params unsafely, breaking the invariant that param normalization is unambiguous, and leading to: signature/param confusion?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/headers.ts` -> `getHeader`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a query string with duplicated/array params
- Exploit idea: processed-query/getHeader collapses a query string with duplicated/array params unsafely
- Invariant to test: param normalization is unambiguous
- Expected Immunefi impact: Signature/param confusion (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: duplicate-param test
