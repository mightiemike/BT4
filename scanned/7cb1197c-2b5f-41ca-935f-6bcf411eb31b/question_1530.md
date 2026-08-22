# Q1530: http/headers — signature length leak

## Question
Can an unprivileged attacker submit a cookie name differing only by trailing space to `canonicalizeValue` in `http/headers.ts` such that isSignedCookieValid returns early for a cookie name differing only by trailing space, breaking the invariant that length-independent verification, and leading to: cookie forgery oracle?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/headers.ts` -> `canonicalizeValue`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a cookie name differing only by trailing space
- Exploit idea: isSignedCookieValid returns early for a cookie name differing only by trailing space
- Invariant to test: length-independent verification
- Expected Immunefi impact: Cookie forgery oracle (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: length-mismatch test
