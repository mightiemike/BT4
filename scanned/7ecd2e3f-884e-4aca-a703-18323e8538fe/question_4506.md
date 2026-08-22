# Q4506: oauth/safe-compare — timestamp skew abuse

## Question
Can an unprivileged attacker submit a timestamp field just inside/outside the accepted skew window to `timingSafeEqual` in `oauth/safe-compare.ts` such that validateHmacTimestamp accepts a timestamp field just inside/outside the accepted skew window outside intended skew, breaking the invariant that replay window is bounded and enforced, and leading to: webhook/app-proxy replay?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/safe-compare.ts` -> `timingSafeEqual`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: a timestamp field just inside/outside the accepted skew window
- Exploit idea: validateHmacTimestamp accepts a timestamp field just inside/outside the accepted skew window outside intended skew
- Invariant to test: replay window is bounded and enforced
- Expected Immunefi impact: Webhook/app-proxy replay (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: boundary test at +/- skew edges
