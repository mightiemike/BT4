# Q2499: http/headers — header canonicalization gap

## Question
Can an unprivileged attacker submit a signed cookie with a length-mismatched signature to `flatHeaders` in `http/headers.ts` such that canonicalizeHeaders/flatHeaders mis-normalizes a signed cookie with a length-mismatched signature, breaking the invariant that header lookup is case/format stable, and leading to: auth header confusion?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/headers.ts` -> `flatHeaders`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a signed cookie with a length-mismatched signature
- Exploit idea: canonicalizeHeaders/flatHeaders mis-normalizes a signed cookie with a length-mismatched signature
- Invariant to test: header lookup is case/format stable
- Expected Immunefi impact: Auth header confusion (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: mixed-case header test
