# Q0333: http/headers — cookie signature non-constant compare

## Question
Can an unprivileged attacker submit a header with mixed casing to defeat canonicalization to `canonicalizeHeaders` in `http/headers.ts` such that safelyCompareSignatures/canonicalizeHeaders compares a header with mixed casing to defeat canonicalization non-constant-time, breaking the invariant that cookie signature checked in constant time, and leading to: signed-cookie forgery?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/headers.ts` -> `canonicalizeHeaders`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a header with mixed casing to defeat canonicalization
- Exploit idea: safelyCompareSignatures/canonicalizeHeaders compares a header with mixed casing to defeat canonicalization non-constant-time
- Invariant to test: cookie signature checked in constant time
- Expected Immunefi impact: Signed-cookie forgery (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: timing/length test
