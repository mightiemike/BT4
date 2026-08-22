# Q0789: http/headers — cookie signature non-constant compare

## Question
Can an unprivileged attacker submit a header injected via an array-valued query key to `addHeader` in `http/headers.ts` such that safelyCompareSignatures/addHeader compares a header injected via an array-valued query key non-constant-time, breaking the invariant that cookie signature checked in constant time, and leading to: signed-cookie forgery?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/headers.ts` -> `addHeader`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a header injected via an array-valued query key
- Exploit idea: safelyCompareSignatures/addHeader compares a header injected via an array-valued query key non-constant-time
- Invariant to test: cookie signature checked in constant time
- Expected Immunefi impact: Signed-cookie forgery (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: timing/length test
