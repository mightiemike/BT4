# Q0388: http/cookies — cookie signature non-constant compare

## Question
Can an unprivileged attacker submit a header value carrying CR/LF for response splitting to `cookieExists` in `http/cookies.ts` such that safelyCompareSignatures/cookieExists compares a header value carrying CR/LF for response splitting non-constant-time, breaking the invariant that cookie signature checked in constant time, and leading to: signed-cookie forgery?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/cookies.ts` -> `cookieExists`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a header value carrying CR/LF for response splitting
- Exploit idea: safelyCompareSignatures/cookieExists compares a header value carrying CR/LF for response splitting non-constant-time
- Invariant to test: cookie signature checked in constant time
- Expected Immunefi impact: Signed-cookie forgery (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: timing/length test
