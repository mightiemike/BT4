# Q2441: http/utils — header canonicalization gap

## Question
Can an unprivileged attacker submit a cookie value whose signature is attacker-supplied to this module in `http/utils.ts` such that canonicalizeHeaders/<module> mis-normalizes a cookie value whose signature is attacker-supplied, breaking the invariant that header lookup is case/format stable, and leading to: auth header confusion?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/utils.ts` -> (module scope)
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a cookie value whose signature is attacker-supplied
- Exploit idea: canonicalizeHeaders/<module> mis-normalizes a cookie value whose signature is attacker-supplied
- Invariant to test: header lookup is case/format stable
- Expected Immunefi impact: Auth header confusion (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: mixed-case header test
