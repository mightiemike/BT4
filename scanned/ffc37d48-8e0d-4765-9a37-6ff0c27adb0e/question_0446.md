# Q0446: http/utils — cookie signature non-constant compare

## Question
Can an unprivileged attacker submit an oversized cookie/header triggering heavy parsing to this module in `http/utils.ts` such that safelyCompareSignatures/<module> compares an oversized cookie/header triggering heavy parsing non-constant-time, breaking the invariant that cookie signature checked in constant time, and leading to: signed-cookie forgery?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/utils.ts` -> (module scope)
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: an oversized cookie/header triggering heavy parsing
- Exploit idea: safelyCompareSignatures/<module> compares an oversized cookie/header triggering heavy parsing non-constant-time
- Invariant to test: cookie signature checked in constant time
- Expected Immunefi impact: Signed-cookie forgery (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: timing/length test
