# Q5625: crypto/utils — array-param smuggling

## Question
Can an unprivileged attacker submit a hex-cased vs lower-cased signature value to `deriveSHA256HMACKey` in `crypto/utils.ts` such that deriveSHA256HMACKey coerces an array-typed signature/param and validates a hex-cased vs lower-cased signature value, breaking the invariant that scalar-only signature parsing, and leading to: forged request accepted?

## Target
- File/function: `packages/apps/shopify-api/runtime/crypto/utils.ts` -> `deriveSHA256HMACKey`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: a hex-cased vs lower-cased signature value
- Exploit idea: deriveSHA256HMACKey coerces an array-typed signature/param and validates a hex-cased vs lower-cased signature value
- Invariant to test: scalar-only signature parsing
- Expected Immunefi impact: Forged request accepted (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: submit array-typed hmac and expect reject
