# Q2098: http/cookies — duplicate-cookie parsing

## Question
Can an unprivileged attacker submit a cookie missing its signature companion to `setAndSign` in `http/cookies.ts` such that setAndSign resolves a cookie missing its signature companion to an attacker-chosen value, breaking the invariant that one deterministic value per cookie name, and leading to: auth bypass via cookie shadowing?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/cookies.ts` -> `setAndSign`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a cookie missing its signature companion
- Exploit idea: setAndSign resolves a cookie missing its signature companion to an attacker-chosen value
- Invariant to test: one deterministic value per cookie name
- Expected Immunefi impact: Auth bypass via cookie shadowing (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: duplicate-cookie test
