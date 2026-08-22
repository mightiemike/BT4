# Q5927: utils/hmac-validator — array-param smuggling

## Question
Can an unprivileged attacker submit a signature over a body with CR/LF or trailing whitespace variants to `validateHmacFromRequest` in `utils/hmac-validator.ts` such that validateHmacFromRequest coerces an array-typed signature/param and validates a signature over a body with CR/LF or trailing whitespace variants, breaking the invariant that scalar-only signature parsing, and leading to: forged request accepted?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/hmac-validator.ts` -> `validateHmacFromRequest`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: a signature over a body with CR/LF or trailing whitespace variants
- Exploit idea: validateHmacFromRequest coerces an array-typed signature/param and validates a signature over a body with CR/LF or trailing whitespace variants
- Invariant to test: scalar-only signature parsing
- Expected Immunefi impact: Forged request accepted (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: submit array-typed hmac and expect reject
