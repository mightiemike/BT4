# Q0845: http/utils — signature length leak

## Question
Can an unprivileged attacker submit a cookie value whose signature is attacker-supplied to this module in `http/utils.ts` such that isSignedCookieValid returns early for a cookie value whose signature is attacker-supplied, breaking the invariant that length-independent verification, and leading to: cookie forgery oracle?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/utils.ts` -> (module scope)
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a cookie value whose signature is attacker-supplied
- Exploit idea: isSignedCookieValid returns early for a cookie value whose signature is attacker-supplied
- Invariant to test: length-independent verification
- Expected Immunefi impact: Cookie forgery oracle (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: length-mismatch test
