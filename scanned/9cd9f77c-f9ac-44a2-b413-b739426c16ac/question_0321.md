# Q0321: helpers/validate-redirect-url — open redirect via shop

## Question
Can an unprivileged attacker submit a shop with a trailing dot, path, or extra label to `isSafe` in `helpers/validate-redirect-url.ts` such that isSafe treats a shop with a trailing dot, path, or extra label as a valid shop and redirects there, breaking the invariant that only allow-listed *.myshopify.com destinations, and leading to: open redirect -> session-token phishing?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts` -> `isSafe`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop with a trailing dot, path, or extra label
- Exploit idea: isSafe treats a shop with a trailing dot, path, or extra label as a valid shop and redirects there
- Invariant to test: only allow-listed *.myshopify.com destinations
- Expected Immunefi impact: Open redirect -> session-token phishing (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: regex bypass test with a shop with a trailing dot, path, or extra label
