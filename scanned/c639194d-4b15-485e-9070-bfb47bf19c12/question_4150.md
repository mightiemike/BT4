# Q4150: http/cookies — query param collision

## Question
Can an unprivileged attacker submit two cookies with the same name (duplicate) parsed ambiguously to `Cookies` in `http/cookies.ts` such that processed-query/getHeader collapses two cookies with the same name (duplicate) parsed ambiguously unsafely, breaking the invariant that param normalization is unambiguous, and leading to: signature/param confusion?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/cookies.ts` -> `Cookies`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: two cookies with the same name (duplicate) parsed ambiguously
- Exploit idea: processed-query/getHeader collapses two cookies with the same name (duplicate) parsed ambiguously unsafely
- Invariant to test: param normalization is unambiguous
- Expected Immunefi impact: Signature/param confusion (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: duplicate-param test
