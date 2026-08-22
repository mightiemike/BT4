# Q5857: http/headers — parse DoS

## Question
Can an unprivileged attacker submit an oversized cookie/header triggering heavy parsing to `removeHeader` in `http/headers.ts` such that removeHeader does super-linear work on an oversized cookie/header triggering heavy parsing, breaking the invariant that bounded header/cookie parsing, and leading to: dos?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/headers.ts` -> `removeHeader`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: an oversized cookie/header triggering heavy parsing
- Exploit idea: removeHeader does super-linear work on an oversized cookie/header triggering heavy parsing
- Invariant to test: bounded header/cookie parsing
- Expected Immunefi impact: DoS (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: oversized-input timing test
