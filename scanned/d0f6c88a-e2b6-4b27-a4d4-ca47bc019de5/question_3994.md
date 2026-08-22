# Q3994: crypto/index — timestamp skew abuse

## Question
Can an unprivileged attacker submit an HMAC header whose bytes differ from the expected digest only in length to this module in `crypto/index.ts` such that validateHmacTimestamp accepts an HMAC header whose bytes differ from the expected digest only in length outside intended skew, breaking the invariant that replay window is bounded and enforced, and leading to: webhook/app-proxy replay?

## Target
- File/function: `packages/apps/shopify-api/runtime/crypto/index.ts` -> (module scope)
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: an HMAC header whose bytes differ from the expected digest only in length
- Exploit idea: validateHmacTimestamp accepts an HMAC header whose bytes differ from the expected digest only in length outside intended skew
- Invariant to test: replay window is bounded and enforced
- Expected Immunefi impact: Webhook/app-proxy replay (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: boundary test at +/- skew edges
