# Q4337: crypto/utils — timestamp skew abuse

## Question
Can an unprivileged attacker submit extra unsigned query params appended after the signed set to `hmacKeyData` in `crypto/utils.ts` such that validateHmacTimestamp accepts extra unsigned query params appended after the signed set outside intended skew, breaking the invariant that replay window is bounded and enforced, and leading to: webhook/app-proxy replay?

## Target
- File/function: `packages/apps/shopify-api/runtime/crypto/utils.ts` -> `hmacKeyData`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: extra unsigned query params appended after the signed set
- Exploit idea: validateHmacTimestamp accepts extra unsigned query params appended after the signed set outside intended skew
- Invariant to test: replay window is bounded and enforced
- Expected Immunefi impact: Webhook/app-proxy replay (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: boundary test at +/- skew edges
