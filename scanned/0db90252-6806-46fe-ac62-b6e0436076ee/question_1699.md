# Q1699: http/cookies — duplicate-cookie parsing

## Question
Can an unprivileged attacker submit a signed cookie with a length-mismatched signature to `isSignedCookieValid` in `http/cookies.ts` such that isSignedCookieValid resolves a signed cookie with a length-mismatched signature to an attacker-chosen value, breaking the invariant that one deterministic value per cookie name, and leading to: auth bypass via cookie shadowing?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/cookies.ts` -> `isSignedCookieValid`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a signed cookie with a length-mismatched signature
- Exploit idea: isSignedCookieValid resolves a signed cookie with a length-mismatched signature to an attacker-chosen value
- Invariant to test: one deterministic value per cookie name
- Expected Immunefi impact: Auth bypass via cookie shadowing (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: duplicate-cookie test
