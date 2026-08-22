# Q5889: http/cookies — parse DoS

## Question
Can an unprivileged attacker submit a cookie missing its signature companion to `safelyCompareSignatures` in `http/cookies.ts` such that safelyCompareSignatures does super-linear work on a cookie missing its signature companion, breaking the invariant that bounded header/cookie parsing, and leading to: dos?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/cookies.ts` -> `safelyCompareSignatures`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a cookie missing its signature companion
- Exploit idea: safelyCompareSignatures does super-linear work on a cookie missing its signature companion
- Invariant to test: bounded header/cookie parsing
- Expected Immunefi impact: DoS (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: oversized-input timing test
