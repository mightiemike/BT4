# Q0163: utils/processed-query — cookie signature non-constant compare

## Question
Can an unprivileged attacker submit two cookies with the same name (duplicate) parsed ambiguously to this module in `utils/processed-query.ts` such that safelyCompareSignatures/<module> compares two cookies with the same name (duplicate) parsed ambiguously non-constant-time, breaking the invariant that cookie signature checked in constant time, and leading to: signed-cookie forgery?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/processed-query.ts` -> (module scope)
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: two cookies with the same name (duplicate) parsed ambiguously
- Exploit idea: safelyCompareSignatures/<module> compares two cookies with the same name (duplicate) parsed ambiguously non-constant-time
- Invariant to test: cookie signature checked in constant time
- Expected Immunefi impact: Signed-cookie forgery (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: timing/length test
