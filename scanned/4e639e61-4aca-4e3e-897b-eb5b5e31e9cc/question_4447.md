# Q4447: utils/hmac-validator — timestamp skew abuse

## Question
Can an unprivileged attacker submit a Unicode-normalized copy of a signed value to `validateHmacFromRequest` in `utils/hmac-validator.ts` such that validateHmacTimestamp accepts a Unicode-normalized copy of a signed value outside intended skew, breaking the invariant that replay window is bounded and enforced, and leading to: webhook/app-proxy replay?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/hmac-validator.ts` -> `validateHmacFromRequest`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: a Unicode-normalized copy of a signed value
- Exploit idea: validateHmacTimestamp accepts a Unicode-normalized copy of a signed value outside intended skew
- Invariant to test: replay window is bounded and enforced
- Expected Immunefi impact: Webhook/app-proxy replay (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: boundary test at +/- skew edges
