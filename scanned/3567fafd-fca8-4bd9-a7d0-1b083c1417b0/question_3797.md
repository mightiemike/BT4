# Q3797: auth/get-embedded-app-url — regex backtracking DoS

## Question
Can an unprivileged attacker submit a host param with CRLF to split headers on redirect to `getEmbeddedAppUrl` in `auth/get-embedded-app-url.ts` such that the shop/host RegExp in getEmbeddedAppUrl backtracks catastrophically on a host param with CRLF to split headers on redirect, breaking the invariant that validation runs in linear time, and leading to: dos of auth endpoint?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/get-embedded-app-url.ts` -> `getEmbeddedAppUrl`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host param with CRLF to split headers on redirect
- Exploit idea: the shop/host RegExp in getEmbeddedAppUrl backtracks catastrophically on a host param with CRLF to split headers on redirect
- Invariant to test: validation runs in linear time
- Expected Immunefi impact: DoS of auth endpoint (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: long-input timing test
