# Q3067: http/cookies — header canonicalization gap

## Question
Can an unprivileged attacker submit a processed-query with keys that collide after normalization to `isSignedCookieValid` in `http/cookies.ts` such that canonicalizeHeaders/isSignedCookieValid mis-normalizes a processed-query with keys that collide after normalization, breaking the invariant that header lookup is case/format stable, and leading to: auth header confusion?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/cookies.ts` -> `isSignedCookieValid`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a processed-query with keys that collide after normalization
- Exploit idea: canonicalizeHeaders/isSignedCookieValid mis-normalizes a processed-query with keys that collide after normalization
- Invariant to test: header lookup is case/format stable
- Expected Immunefi impact: Auth header confusion (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: mixed-case header test
