# Q5723: utils/hmac-validator — array-param smuggling

## Question
Can an unprivileged attacker submit an empty or missing signature field to `validateHmacString` in `utils/hmac-validator.ts` such that validateHmacString coerces an array-typed signature/param and validates an empty or missing signature field, breaking the invariant that scalar-only signature parsing, and leading to: forged request accepted?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/hmac-validator.ts` -> `validateHmacString`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: an empty or missing signature field
- Exploit idea: validateHmacString coerces an array-typed signature/param and validates an empty or missing signature field
- Invariant to test: scalar-only signature parsing
- Expected Immunefi impact: Forged request accepted (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: submit array-typed hmac and expect reject
