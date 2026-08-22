# Q4321: http/cookies — query param collision

## Question
Can an unprivileged attacker submit a header with mixed casing to defeat canonicalization to `getAndVerify` in `http/cookies.ts` such that processed-query/getHeader collapses a header with mixed casing to defeat canonicalization unsafely, breaking the invariant that param normalization is unambiguous, and leading to: signature/param confusion?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/cookies.ts` -> `getAndVerify`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a header with mixed casing to defeat canonicalization
- Exploit idea: processed-query/getHeader collapses a header with mixed casing to defeat canonicalization unsafely
- Invariant to test: param normalization is unambiguous
- Expected Immunefi impact: Signature/param confusion (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: duplicate-param test
