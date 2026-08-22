# Q5961: utils/hmac-validator — array-param smuggling

## Question
Can an unprivileged attacker submit mixed encoding of '+' vs '%20' in the pre-hash string to `validateHmacTimestamp` in `utils/hmac-validator.ts` such that validateHmacTimestamp coerces an array-typed signature/param and validates mixed encoding of '+' vs '%20' in the pre-hash string, breaking the invariant that scalar-only signature parsing, and leading to: forged request accepted?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/hmac-validator.ts` -> `validateHmacTimestamp`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: mixed encoding of '+' vs '%20' in the pre-hash string
- Exploit idea: validateHmacTimestamp coerces an array-typed signature/param and validates mixed encoding of '+' vs '%20' in the pre-hash string
- Invariant to test: scalar-only signature parsing
- Expected Immunefi impact: Forged request accepted (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: submit array-typed hmac and expect reject
