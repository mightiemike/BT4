# Q5591: crypto/utils — array-param smuggling

## Question
Can an unprivileged attacker submit an HMAC header whose bytes differ from the expected digest only in length to `createSHA256HMAC` in `crypto/utils.ts` such that createSHA256HMAC coerces an array-typed signature/param and validates an HMAC header whose bytes differ from the expected digest only in length, breaking the invariant that scalar-only signature parsing, and leading to: forged request accepted?

## Target
- File/function: `packages/apps/shopify-api/runtime/crypto/utils.ts` -> `createSHA256HMAC`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: an HMAC header whose bytes differ from the expected digest only in length
- Exploit idea: createSHA256HMAC coerces an array-typed signature/param and validates an HMAC header whose bytes differ from the expected digest only in length
- Invariant to test: scalar-only signature parsing
- Expected Immunefi impact: Forged request accepted (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: submit array-typed hmac and expect reject
