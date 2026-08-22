# Q5958: http/utils — parse DoS

## Question
Can an unprivileged attacker submit a request with conflicting Host vs X-Forwarded-Host to this module in `http/utils.ts` such that <module> does super-linear work on a request with conflicting Host vs X-Forwarded-Host, breaking the invariant that bounded header/cookie parsing, and leading to: dos?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/utils.ts` -> (module scope)
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a request with conflicting Host vs X-Forwarded-Host
- Exploit idea: <module> does super-linear work on a request with conflicting Host vs X-Forwarded-Host
- Invariant to test: bounded header/cookie parsing
- Expected Immunefi impact: DoS (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: oversized-input timing test
