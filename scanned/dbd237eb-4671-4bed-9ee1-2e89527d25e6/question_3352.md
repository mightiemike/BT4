# Q3352: http/cookies — response splitting

## Question
Can an unprivileged attacker submit two cookies with the same name (duplicate) parsed ambiguously to `safelyCompareSignatures` in `http/cookies.ts` such that setHeader/addHeader emits two cookies with the same name (duplicate) parsed ambiguously with CR/LF, breaking the invariant that header values are sanitized, and leading to: header injection / redirect?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/cookies.ts` -> `safelyCompareSignatures`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: two cookies with the same name (duplicate) parsed ambiguously
- Exploit idea: setHeader/addHeader emits two cookies with the same name (duplicate) parsed ambiguously with CR/LF
- Invariant to test: header values are sanitized
- Expected Immunefi impact: Header injection / redirect (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: CRLF test
