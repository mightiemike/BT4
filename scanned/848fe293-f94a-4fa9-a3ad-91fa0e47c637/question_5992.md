# Q5992: http/utils — parse DoS

## Question
Can an unprivileged attacker submit a processed-query with keys that collide after normalization to this module in `http/utils.ts` such that <module> does super-linear work on a processed-query with keys that collide after normalization, breaking the invariant that bounded header/cookie parsing, and leading to: dos?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/utils.ts` -> (module scope)
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a processed-query with keys that collide after normalization
- Exploit idea: <module> does super-linear work on a processed-query with keys that collide after normalization
- Invariant to test: bounded header/cookie parsing
- Expected Immunefi impact: DoS (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: oversized-input timing test
