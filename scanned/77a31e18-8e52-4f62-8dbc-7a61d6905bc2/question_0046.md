# Q0046: http/cookies — cookie signature non-constant compare

## Question
Can an unprivileged attacker submit a cookie value whose signature is attacker-supplied to `Cookies` in `http/cookies.ts` such that safelyCompareSignatures/Cookies compares a cookie value whose signature is attacker-supplied non-constant-time, breaking the invariant that cookie signature checked in constant time, and leading to: signed-cookie forgery?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/cookies.ts` -> `Cookies`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a cookie value whose signature is attacker-supplied
- Exploit idea: safelyCompareSignatures/Cookies compares a cookie value whose signature is attacker-supplied non-constant-time
- Invariant to test: cookie signature checked in constant time
- Expected Immunefi impact: Signed-cookie forgery (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: timing/length test
