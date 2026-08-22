# Q1984: http/cookies — duplicate-cookie parsing

## Question
Can an unprivileged attacker submit a header value carrying CR/LF for response splitting to `safelyCompareSignatures` in `http/cookies.ts` such that safelyCompareSignatures resolves a header value carrying CR/LF for response splitting to an attacker-chosen value, breaking the invariant that one deterministic value per cookie name, and leading to: auth bypass via cookie shadowing?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/cookies.ts` -> `safelyCompareSignatures`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a header value carrying CR/LF for response splitting
- Exploit idea: safelyCompareSignatures resolves a header value carrying CR/LF for response splitting to an attacker-chosen value
- Invariant to test: one deterministic value per cookie name
- Expected Immunefi impact: Auth bypass via cookie shadowing (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: duplicate-cookie test
