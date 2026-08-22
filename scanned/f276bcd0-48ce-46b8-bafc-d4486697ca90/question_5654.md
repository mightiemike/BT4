# Q5654: utils/processed-query — parse DoS

## Question
Can an unprivileged attacker submit a signed cookie with a length-mismatched signature to this module in `utils/processed-query.ts` such that <module> does super-linear work on a signed cookie with a length-mismatched signature, breaking the invariant that bounded header/cookie parsing, and leading to: dos?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/processed-query.ts` -> (module scope)
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a signed cookie with a length-mismatched signature
- Exploit idea: <module> does super-linear work on a signed cookie with a length-mismatched signature
- Invariant to test: bounded header/cookie parsing
- Expected Immunefi impact: DoS (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: oversized-input timing test
