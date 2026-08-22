# Q1255: utils/hmac-validator — length-leak in compare

## Question
Can an unprivileged attacker submit a Unicode-normalized copy of a signed value to `validateHmacString` in `utils/hmac-validator.ts` such that validateHmacString returns early when signature lengths differ, leaking validity, breaking the invariant that timing/length independence of HMAC check, and leading to: auth bypass via signature oracle?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/hmac-validator.ts` -> `validateHmacString`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: a Unicode-normalized copy of a signed value
- Exploit idea: validateHmacString returns early when signature lengths differ, leaking validity
- Invariant to test: timing/length independence of HMAC check
- Expected Immunefi impact: Auth bypass via signature oracle (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: unit test asserting equal-time handling of mismatched lengths
