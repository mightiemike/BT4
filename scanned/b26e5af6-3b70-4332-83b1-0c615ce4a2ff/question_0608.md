# Q0608: helpers/get-shop-from-request — open redirect via shop

## Question
Can an unprivileged attacker submit a host param with CRLF to split headers on redirect to `getShopFromRequest` in `helpers/get-shop-from-request.ts` such that getShopFromRequest treats a host param with CRLF to split headers on redirect as a valid shop and redirects there, breaking the invariant that only allow-listed *.myshopify.com destinations, and leading to: open redirect -> session-token phishing?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/get-shop-from-request.ts` -> `getShopFromRequest`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host param with CRLF to split headers on redirect
- Exploit idea: getShopFromRequest treats a host param with CRLF to split headers on redirect as a valid shop and redirects there
- Invariant to test: only allow-listed *.myshopify.com destinations
- Expected Immunefi impact: Open redirect -> session-token phishing (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: regex bypass test with a host param with CRLF to split headers on redirect
