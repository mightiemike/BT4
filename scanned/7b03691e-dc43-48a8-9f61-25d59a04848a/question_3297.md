# Q3297: http/headers — response splitting

## Question
Can an unprivileged attacker submit a signed cookie with a length-mismatched signature to `setHeader` in `http/headers.ts` such that setHeader/addHeader emits a signed cookie with a length-mismatched signature with CR/LF, breaking the invariant that header values are sanitized, and leading to: header injection / redirect?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/headers.ts` -> `setHeader`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a signed cookie with a length-mismatched signature
- Exploit idea: setHeader/addHeader emits a signed cookie with a length-mismatched signature with CR/LF
- Invariant to test: header values are sanitized
- Expected Immunefi impact: Header injection / redirect (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: CRLF test
