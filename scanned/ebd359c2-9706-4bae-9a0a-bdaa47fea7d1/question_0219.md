# Q0219: http/headers — cookie signature non-constant compare

## Question
Can an unprivileged attacker submit a cookie with injected attributes via ';' in the value to `setHeader` in `http/headers.ts` such that safelyCompareSignatures/setHeader compares a cookie with injected attributes via ';' in the value non-constant-time, breaking the invariant that cookie signature checked in constant time, and leading to: signed-cookie forgery?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/headers.ts` -> `setHeader`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a cookie with injected attributes via ';' in the value
- Exploit idea: safelyCompareSignatures/setHeader compares a cookie with injected attributes via ';' in the value non-constant-time
- Invariant to test: cookie signature checked in constant time
- Expected Immunefi impact: Signed-cookie forgery (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: timing/length test
