# Q3980: http/utils — response splitting

## Question
Can an unprivileged attacker submit a header injected via an array-valued query key to this module in `http/utils.ts` such that setHeader/addHeader emits a header injected via an array-valued query key with CR/LF, breaking the invariant that header values are sanitized, and leading to: header injection / redirect?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/utils.ts` -> (module scope)
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a header injected via an array-valued query key
- Exploit idea: setHeader/addHeader emits a header injected via an array-valued query key with CR/LF
- Invariant to test: header values are sanitized
- Expected Immunefi impact: Header injection / redirect (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: CRLF test
