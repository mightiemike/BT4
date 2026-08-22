# Q2440: http/cookies — header canonicalization gap

## Question
Can an unprivileged attacker submit a cookie value whose signature is attacker-supplied to `safelyCompareSignatures` in `http/cookies.ts` such that canonicalizeHeaders/safelyCompareSignatures mis-normalizes a cookie value whose signature is attacker-supplied, breaking the invariant that header lookup is case/format stable, and leading to: auth header confusion?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/cookies.ts` -> `safelyCompareSignatures`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a cookie value whose signature is attacker-supplied
- Exploit idea: canonicalizeHeaders/safelyCompareSignatures mis-normalizes a cookie value whose signature is attacker-supplied
- Invariant to test: header lookup is case/format stable
- Expected Immunefi impact: Auth header confusion (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: mixed-case header test
