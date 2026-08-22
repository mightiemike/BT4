# Q4720: http/cookies — query param collision

## Question
Can an unprivileged attacker submit a cookie name differing only by trailing space to `safelyCompareSignatures` in `http/cookies.ts` such that processed-query/getHeader collapses a cookie name differing only by trailing space unsafely, breaking the invariant that param normalization is unambiguous, and leading to: signature/param confusion?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/cookies.ts` -> `safelyCompareSignatures`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a cookie name differing only by trailing space
- Exploit idea: processed-query/getHeader collapses a cookie name differing only by trailing space unsafely
- Invariant to test: param normalization is unambiguous
- Expected Immunefi impact: Signature/param confusion (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: duplicate-param test
