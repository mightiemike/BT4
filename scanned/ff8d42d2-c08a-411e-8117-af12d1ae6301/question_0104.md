# Q0104: http/utils — cookie signature non-constant compare

## Question
Can an unprivileged attacker submit a signed cookie with a length-mismatched signature to this module in `http/utils.ts` such that safelyCompareSignatures/<module> compares a signed cookie with a length-mismatched signature non-constant-time, breaking the invariant that cookie signature checked in constant time, and leading to: signed-cookie forgery?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/utils.ts` -> (module scope)
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a signed cookie with a length-mismatched signature
- Exploit idea: safelyCompareSignatures/<module> compares a signed cookie with a length-mismatched signature non-constant-time
- Invariant to test: cookie signature checked in constant time
- Expected Immunefi impact: Signed-cookie forgery (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: timing/length test
