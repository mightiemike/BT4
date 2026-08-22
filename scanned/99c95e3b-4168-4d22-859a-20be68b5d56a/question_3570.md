# Q3570: helpers/validate-redirect-url — regex backtracking DoS

## Question
Can an unprivileged attacker submit a host that passes the '.myshopify.com$' suffix test via subdomain to `sanitizeRedirectUrl` in `helpers/validate-redirect-url.ts` such that the shop/host RegExp in sanitizeRedirectUrl backtracks catastrophically on a host that passes the '.myshopify.com$' suffix test via subdomain, breaking the invariant that validation runs in linear time, and leading to: dos of auth endpoint?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts` -> `sanitizeRedirectUrl`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host that passes the '.myshopify.com$' suffix test via subdomain
- Exploit idea: the shop/host RegExp in sanitizeRedirectUrl backtracks catastrophically on a host that passes the '.myshopify.com$' suffix test via subdomain
- Invariant to test: validation runs in linear time
- Expected Immunefi impact: DoS of auth endpoint (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: long-input timing test
