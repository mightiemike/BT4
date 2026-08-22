# Q5691: oauth/safe-compare — array-param smuggling

## Question
Can an unprivileged attacker submit duplicated query keys (?hmac=a&hmac=b) reordered before signing to `timingSafeEqual` in `oauth/safe-compare.ts` such that timingSafeEqual coerces an array-typed signature/param and validates duplicated query keys (?hmac=a&hmac=b) reordered before signing, breaking the invariant that scalar-only signature parsing, and leading to: forged request accepted?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/safe-compare.ts` -> `timingSafeEqual`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: duplicated query keys (?hmac=a&hmac=b) reordered before signing
- Exploit idea: timingSafeEqual coerces an array-typed signature/param and validates duplicated query keys (?hmac=a&hmac=b) reordered before signing
- Invariant to test: scalar-only signature parsing
- Expected Immunefi impact: Forged request accepted (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: submit array-typed hmac and expect reject
