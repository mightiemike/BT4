# Q4277: utils/get-hmac-key — timestamp skew abuse

## Question
Can an unprivileged attacker submit a signature computed over a differently-encoded query string to `getHMACKey` in `utils/get-hmac-key.ts` such that validateHmacTimestamp accepts a signature computed over a differently-encoded query string outside intended skew, breaking the invariant that replay window is bounded and enforced, and leading to: webhook/app-proxy replay?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/get-hmac-key.ts` -> `getHMACKey`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: a signature computed over a differently-encoded query string
- Exploit idea: validateHmacTimestamp accepts a signature computed over a differently-encoded query string outside intended skew
- Invariant to test: replay window is bounded and enforced
- Expected Immunefi impact: Webhook/app-proxy replay (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: boundary test at +/- skew edges
