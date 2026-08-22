# Q4108: crypto/index — timestamp skew abuse

## Question
Can an unprivileged attacker submit a base64 signature carrying trailing '=' padding or URL-safe chars to this module in `crypto/index.ts` such that validateHmacTimestamp accepts a base64 signature carrying trailing '=' padding or URL-safe chars outside intended skew, breaking the invariant that replay window is bounded and enforced, and leading to: webhook/app-proxy replay?

## Target
- File/function: `packages/apps/shopify-api/runtime/crypto/index.ts` -> (module scope)
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: a base64 signature carrying trailing '=' padding or URL-safe chars
- Exploit idea: validateHmacTimestamp accepts a base64 signature carrying trailing '=' padding or URL-safe chars outside intended skew
- Invariant to test: replay window is bounded and enforced
- Expected Immunefi impact: Webhook/app-proxy replay (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: boundary test at +/- skew edges
