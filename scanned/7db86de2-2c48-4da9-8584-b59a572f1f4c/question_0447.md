# Q0447: http/headers — cookie signature non-constant compare

## Question
Can an unprivileged attacker submit an oversized cookie/header triggering heavy parsing to `flatHeaders` in `http/headers.ts` such that safelyCompareSignatures/flatHeaders compares an oversized cookie/header triggering heavy parsing non-constant-time, breaking the invariant that cookie signature checked in constant time, and leading to: signed-cookie forgery?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/headers.ts` -> `flatHeaders`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: an oversized cookie/header triggering heavy parsing
- Exploit idea: safelyCompareSignatures/flatHeaders compares an oversized cookie/header triggering heavy parsing non-constant-time
- Invariant to test: cookie signature checked in constant time
- Expected Immunefi impact: Signed-cookie forgery (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: timing/length test
