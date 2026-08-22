# Q4437: http/headers — query param collision

## Question
Can an unprivileged attacker submit an oversized cookie/header triggering heavy parsing to `canonicalizeHeaders` in `http/headers.ts` such that processed-query/getHeader collapses an oversized cookie/header triggering heavy parsing unsafely, breaking the invariant that param normalization is unambiguous, and leading to: signature/param confusion?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/headers.ts` -> `canonicalizeHeaders`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: an oversized cookie/header triggering heavy parsing
- Exploit idea: processed-query/getHeader collapses an oversized cookie/header triggering heavy parsing unsafely
- Invariant to test: param normalization is unambiguous
- Expected Immunefi impact: Signature/param confusion (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: duplicate-param test
