# Q2611: http/cookies — header canonicalization gap

## Question
Can an unprivileged attacker submit a cookie with injected attributes via ';' in the value to `isSignedCookieValid` in `http/cookies.ts` such that canonicalizeHeaders/isSignedCookieValid mis-normalizes a cookie with injected attributes via ';' in the value, breaking the invariant that header lookup is case/format stable, and leading to: auth header confusion?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/cookies.ts` -> `isSignedCookieValid`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a cookie with injected attributes via ';' in the value
- Exploit idea: canonicalizeHeaders/isSignedCookieValid mis-normalizes a cookie with injected attributes via ';' in the value
- Invariant to test: header lookup is case/format stable
- Expected Immunefi impact: Auth header confusion (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: mixed-case header test
