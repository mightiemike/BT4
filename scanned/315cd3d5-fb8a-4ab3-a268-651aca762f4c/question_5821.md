# Q5821: http/cookies — parse DoS

## Question
Can an unprivileged attacker submit a header value carrying CR/LF for response splitting to `Cookies` in `http/cookies.ts` such that Cookies does super-linear work on a header value carrying CR/LF for response splitting, breaking the invariant that bounded header/cookie parsing, and leading to: dos?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/cookies.ts` -> `Cookies`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a header value carrying CR/LF for response splitting
- Exploit idea: Cookies does super-linear work on a header value carrying CR/LF for response splitting
- Invariant to test: bounded header/cookie parsing
- Expected Immunefi impact: DoS (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: oversized-input timing test
