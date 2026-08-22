# Q1357: http/cookies — signature length leak

## Question
Can an unprivileged attacker submit a percent-encoded key that decodes to a reserved name to `deleteInvalidCookies` in `http/cookies.ts` such that isSignedCookieValid returns early for a percent-encoded key that decodes to a reserved name, breaking the invariant that length-independent verification, and leading to: cookie forgery oracle?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/cookies.ts` -> `deleteInvalidCookies`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a percent-encoded key that decodes to a reserved name
- Exploit idea: isSignedCookieValid returns early for a percent-encoded key that decodes to a reserved name
- Invariant to test: length-independent verification
- Expected Immunefi impact: Cookie forgery oracle (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: length-mismatch test
