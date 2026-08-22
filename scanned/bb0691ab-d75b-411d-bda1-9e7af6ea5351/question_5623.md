# Q5623: oauth/safe-compare — array-param smuggling

## Question
Can an unprivileged attacker submit a hex-cased vs lower-cased signature value to `timingSafeEqual` in `oauth/safe-compare.ts` such that timingSafeEqual coerces an array-typed signature/param and validates a hex-cased vs lower-cased signature value, breaking the invariant that scalar-only signature parsing, and leading to: forged request accepted?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/safe-compare.ts` -> `timingSafeEqual`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: a hex-cased vs lower-cased signature value
- Exploit idea: timingSafeEqual coerces an array-typed signature/param and validates a hex-cased vs lower-cased signature value
- Invariant to test: scalar-only signature parsing
- Expected Immunefi impact: Forged request accepted (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: submit array-typed hmac and expect reject
