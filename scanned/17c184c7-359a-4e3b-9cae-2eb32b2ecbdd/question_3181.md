# Q3181: http/cookies — header canonicalization gap

## Question
Can an unprivileged attacker submit a header injected via an array-valued query key to `deleteInvalidCookies` in `http/cookies.ts` such that canonicalizeHeaders/deleteInvalidCookies mis-normalizes a header injected via an array-valued query key, breaking the invariant that header lookup is case/format stable, and leading to: auth header confusion?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/cookies.ts` -> `deleteInvalidCookies`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a header injected via an array-valued query key
- Exploit idea: canonicalizeHeaders/deleteInvalidCookies mis-normalizes a header injected via an array-valued query key
- Invariant to test: header lookup is case/format stable
- Expected Immunefi impact: Auth header confusion (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: mixed-case header test
