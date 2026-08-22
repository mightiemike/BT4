# Q1243: http/cookies — signature length leak

## Question
Can an unprivileged attacker submit an oversized cookie/header triggering heavy parsing to `isSignedCookieValid` in `http/cookies.ts` such that isSignedCookieValid returns early for an oversized cookie/header triggering heavy parsing, breaking the invariant that length-independent verification, and leading to: cookie forgery oracle?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/cookies.ts` -> `isSignedCookieValid`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: an oversized cookie/header triggering heavy parsing
- Exploit idea: isSignedCookieValid returns early for an oversized cookie/header triggering heavy parsing
- Invariant to test: length-independent verification
- Expected Immunefi impact: Cookie forgery oracle (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: length-mismatch test
