# Q3696: http/headers — response splitting

## Question
Can an unprivileged attacker submit a cookie missing its signature companion to `getHeaders` in `http/headers.ts` such that setHeader/addHeader emits a cookie missing its signature companion with CR/LF, breaking the invariant that header values are sanitized, and leading to: header injection / redirect?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/headers.ts` -> `getHeaders`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a cookie missing its signature companion
- Exploit idea: setHeader/addHeader emits a cookie missing its signature companion with CR/LF
- Invariant to test: header values are sanitized
- Expected Immunefi impact: Header injection / redirect (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: CRLF test
