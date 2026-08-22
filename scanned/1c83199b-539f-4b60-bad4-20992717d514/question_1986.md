# Q1986: http/headers — duplicate-cookie parsing

## Question
Can an unprivileged attacker submit a header value carrying CR/LF for response splitting to `flatHeaders` in `http/headers.ts` such that flatHeaders resolves a header value carrying CR/LF for response splitting to an attacker-chosen value, breaking the invariant that one deterministic value per cookie name, and leading to: auth bypass via cookie shadowing?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/headers.ts` -> `flatHeaders`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a header value carrying CR/LF for response splitting
- Exploit idea: flatHeaders resolves a header value carrying CR/LF for response splitting to an attacker-chosen value
- Invariant to test: one deterministic value per cookie name
- Expected Immunefi impact: Auth bypass via cookie shadowing (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: duplicate-cookie test
