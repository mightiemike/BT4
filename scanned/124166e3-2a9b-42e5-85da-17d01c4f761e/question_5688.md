# Q5688: utils/processed-query — parse DoS

## Question
Can an unprivileged attacker submit two cookies with the same name (duplicate) parsed ambiguously to this module in `utils/processed-query.ts` such that <module> does super-linear work on two cookies with the same name (duplicate) parsed ambiguously, breaking the invariant that bounded header/cookie parsing, and leading to: dos?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/processed-query.ts` -> (module scope)
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: two cookies with the same name (duplicate) parsed ambiguously
- Exploit idea: <module> does super-linear work on two cookies with the same name (duplicate) parsed ambiguously
- Invariant to test: bounded header/cookie parsing
- Expected Immunefi impact: DoS (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: oversized-input timing test
