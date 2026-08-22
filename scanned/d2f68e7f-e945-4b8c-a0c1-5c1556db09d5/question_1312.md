# Q1312: utils/hmac-validator — length-leak in compare

## Question
Can an unprivileged attacker submit a timestamp field just inside/outside the accepted skew window to `getCurrentTimeInSec` in `utils/hmac-validator.ts` such that getCurrentTimeInSec returns early when signature lengths differ, leaking validity, breaking the invariant that timing/length independence of HMAC check, and leading to: auth bypass via signature oracle?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/hmac-validator.ts` -> `getCurrentTimeInSec`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: a timestamp field just inside/outside the accepted skew window
- Exploit idea: getCurrentTimeInSec returns early when signature lengths differ, leaking validity
- Invariant to test: timing/length independence of HMAC check
- Expected Immunefi impact: Auth bypass via signature oracle (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: unit test asserting equal-time handling of mismatched lengths
