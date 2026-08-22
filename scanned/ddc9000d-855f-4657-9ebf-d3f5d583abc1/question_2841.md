# Q2841: http/headers — header canonicalization gap

## Question
Can an unprivileged attacker submit an oversized cookie/header triggering heavy parsing to `addHeader` in `http/headers.ts` such that canonicalizeHeaders/addHeader mis-normalizes an oversized cookie/header triggering heavy parsing, breaking the invariant that header lookup is case/format stable, and leading to: auth header confusion?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/headers.ts` -> `addHeader`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: an oversized cookie/header triggering heavy parsing
- Exploit idea: canonicalizeHeaders/addHeader mis-normalizes an oversized cookie/header triggering heavy parsing
- Invariant to test: header lookup is case/format stable
- Expected Immunefi impact: Auth header confusion (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: mixed-case header test
