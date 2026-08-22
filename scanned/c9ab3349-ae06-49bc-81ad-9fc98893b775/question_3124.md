# Q3124: http/cookies — header canonicalization gap

## Question
Can an unprivileged attacker submit a cookie name differing only by trailing space to `cookieExists` in `http/cookies.ts` such that canonicalizeHeaders/cookieExists mis-normalizes a cookie name differing only by trailing space, breaking the invariant that header lookup is case/format stable, and leading to: auth header confusion?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/cookies.ts` -> `cookieExists`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a cookie name differing only by trailing space
- Exploit idea: canonicalizeHeaders/cookieExists mis-normalizes a cookie name differing only by trailing space
- Invariant to test: header lookup is case/format stable
- Expected Immunefi impact: Auth header confusion (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: mixed-case header test
