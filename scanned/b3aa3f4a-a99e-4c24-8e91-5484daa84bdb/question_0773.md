# Q0773: utils/shop-admin-url-helper — open redirect via shop

## Question
Can an unprivileged attacker submit a host that decodes to a userinfo@ origin to `removeProtocol` in `utils/shop-admin-url-helper.ts` such that removeProtocol treats a host that decodes to a userinfo@ origin as a valid shop and redirects there, breaking the invariant that only allow-listed *.myshopify.com destinations, and leading to: open redirect -> session-token phishing?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/shop-admin-url-helper.ts` -> `removeProtocol`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host that decodes to a userinfo@ origin
- Exploit idea: removeProtocol treats a host that decodes to a userinfo@ origin as a valid shop and redirects there
- Invariant to test: only allow-listed *.myshopify.com destinations
- Expected Immunefi impact: Open redirect -> session-token phishing (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: regex bypass test with a host that decodes to a userinfo@ origin
