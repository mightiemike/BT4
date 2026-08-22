# Q2726: http/utils — header canonicalization gap

## Question
Can an unprivileged attacker submit a header with mixed casing to defeat canonicalization to this module in `http/utils.ts` such that canonicalizeHeaders/<module> mis-normalizes a header with mixed casing to defeat canonicalization, breaking the invariant that header lookup is case/format stable, and leading to: auth header confusion?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/utils.ts` -> (module scope)
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a header with mixed casing to defeat canonicalization
- Exploit idea: canonicalizeHeaders/<module> mis-normalizes a header with mixed casing to defeat canonicalization
- Invariant to test: header lookup is case/format stable
- Expected Immunefi impact: Auth header confusion (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: mixed-case header test
