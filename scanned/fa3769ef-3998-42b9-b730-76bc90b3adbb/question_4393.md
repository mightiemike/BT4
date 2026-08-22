# Q4393: crypto/index — timestamp skew abuse

## Question
Can an unprivileged attacker submit an array-typed hmac param instead of a string to this module in `crypto/index.ts` such that validateHmacTimestamp accepts an array-typed hmac param instead of a string outside intended skew, breaking the invariant that replay window is bounded and enforced, and leading to: webhook/app-proxy replay?

## Target
- File/function: `packages/apps/shopify-api/runtime/crypto/index.ts` -> (module scope)
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: an array-typed hmac param instead of a string
- Exploit idea: validateHmacTimestamp accepts an array-typed hmac param instead of a string outside intended skew
- Invariant to test: replay window is bounded and enforced
- Expected Immunefi impact: Webhook/app-proxy replay (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: boundary test at +/- skew edges
