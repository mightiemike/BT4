# Q5791: utils/hmac-validator — array-param smuggling

## Question
Can an unprivileged attacker submit extra unsigned query params appended after the signed set to `validateHmacFromRequestFactory` in `utils/hmac-validator.ts` such that validateHmacFromRequestFactory coerces an array-typed signature/param and validates extra unsigned query params appended after the signed set, breaking the invariant that scalar-only signature parsing, and leading to: forged request accepted?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/hmac-validator.ts` -> `validateHmacFromRequestFactory`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: extra unsigned query params appended after the signed set
- Exploit idea: validateHmacFromRequestFactory coerces an array-typed signature/param and validates extra unsigned query params appended after the signed set
- Invariant to test: scalar-only signature parsing
- Expected Immunefi impact: Forged request accepted (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: submit array-typed hmac and expect reject
