# Q2898: http/headers — header canonicalization gap

## Question
Can an unprivileged attacker submit a cookie missing its signature companion to `canonicalizeHeaders` in `http/headers.ts` such that canonicalizeHeaders/canonicalizeHeaders mis-normalizes a cookie missing its signature companion, breaking the invariant that header lookup is case/format stable, and leading to: auth header confusion?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/headers.ts` -> `canonicalizeHeaders`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a cookie missing its signature companion
- Exploit idea: canonicalizeHeaders/canonicalizeHeaders mis-normalizes a cookie missing its signature companion
- Invariant to test: header lookup is case/format stable
- Expected Immunefi impact: Auth header confusion (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: mixed-case header test
