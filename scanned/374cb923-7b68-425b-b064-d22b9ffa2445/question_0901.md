# Q0901: http/cookies — signature length leak

## Question
Can an unprivileged attacker submit a signed cookie with a length-mismatched signature to `deleteInvalidCookies` in `http/cookies.ts` such that isSignedCookieValid returns early for a signed cookie with a length-mismatched signature, breaking the invariant that length-independent verification, and leading to: cookie forgery oracle?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/cookies.ts` -> `deleteInvalidCookies`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a signed cookie with a length-mismatched signature
- Exploit idea: isSignedCookieValid returns early for a signed cookie with a length-mismatched signature
- Invariant to test: length-independent verification
- Expected Immunefi impact: Cookie forgery oracle (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: length-mismatch test
