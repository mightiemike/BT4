# Q1130: http/utils — signature length leak

## Question
Can an unprivileged attacker submit a header with mixed casing to defeat canonicalization to this module in `http/utils.ts` such that isSignedCookieValid returns early for a header with mixed casing to defeat canonicalization, breaking the invariant that length-independent verification, and leading to: cookie forgery oracle?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/utils.ts` -> (module scope)
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a header with mixed casing to defeat canonicalization
- Exploit idea: isSignedCookieValid returns early for a header with mixed casing to defeat canonicalization
- Invariant to test: length-independent verification
- Expected Immunefi impact: Cookie forgery oracle (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: length-mismatch test
