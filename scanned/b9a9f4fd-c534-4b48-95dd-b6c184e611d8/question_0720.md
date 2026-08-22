# Q0720: helpers/validate-redirect-url — open redirect via shop

## Question
Can an unprivileged attacker submit a shop param with an embedded null byte before .myshopify.com to `sanitizeRedirectUrl` in `helpers/validate-redirect-url.ts` such that sanitizeRedirectUrl treats a shop param with an embedded null byte before .myshopify.com as a valid shop and redirects there, breaking the invariant that only allow-listed *.myshopify.com destinations, and leading to: open redirect -> session-token phishing?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts` -> `sanitizeRedirectUrl`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop param with an embedded null byte before .myshopify.com
- Exploit idea: sanitizeRedirectUrl treats a shop param with an embedded null byte before .myshopify.com as a valid shop and redirects there
- Invariant to test: only allow-listed *.myshopify.com destinations
- Expected Immunefi impact: Open redirect -> session-token phishing (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: regex bypass test with a shop param with an embedded null byte before .myshopify.com
