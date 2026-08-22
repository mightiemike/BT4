# Q5753: http/cookies — parse DoS

## Question
Can an unprivileged attacker submit a query string with duplicated/array params to `cookieExists` in `http/cookies.ts` such that cookieExists does super-linear work on a query string with duplicated/array params, breaking the invariant that bounded header/cookie parsing, and leading to: dos?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/cookies.ts` -> `cookieExists`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a query string with duplicated/array params
- Exploit idea: cookieExists does super-linear work on a query string with duplicated/array params
- Invariant to test: bounded header/cookie parsing
- Expected Immunefi impact: DoS (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: oversized-input timing test
