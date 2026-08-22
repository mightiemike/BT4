# Q4162: utils/hmac-validator — timestamp skew abuse

## Question
Can an unprivileged attacker submit duplicated query keys (?hmac=a&hmac=b) reordered before signing to `getCurrentTimeInSec` in `utils/hmac-validator.ts` such that validateHmacTimestamp accepts duplicated query keys (?hmac=a&hmac=b) reordered before signing outside intended skew, breaking the invariant that replay window is bounded and enforced, and leading to: webhook/app-proxy replay?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/hmac-validator.ts` -> `getCurrentTimeInSec`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: duplicated query keys (?hmac=a&hmac=b) reordered before signing
- Exploit idea: validateHmacTimestamp accepts duplicated query keys (?hmac=a&hmac=b) reordered before signing outside intended skew
- Invariant to test: replay window is bounded and enforced
- Expected Immunefi impact: Webhook/app-proxy replay (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: boundary test at +/- skew edges
