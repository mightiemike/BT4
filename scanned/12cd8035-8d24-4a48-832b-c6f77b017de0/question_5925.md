# Q5925: http/headers — parse DoS

## Question
Can an unprivileged attacker submit a percent-encoded key that decodes to a reserved name to `canonicalizeValue` in `http/headers.ts` such that canonicalizeValue does super-linear work on a percent-encoded key that decodes to a reserved name, breaking the invariant that bounded header/cookie parsing, and leading to: dos?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/headers.ts` -> `canonicalizeValue`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a percent-encoded key that decodes to a reserved name
- Exploit idea: canonicalizeValue does super-linear work on a percent-encoded key that decodes to a reserved name
- Invariant to test: bounded header/cookie parsing
- Expected Immunefi impact: DoS (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: oversized-input timing test
