# Q4850: crypto/utils — truthy-coercion of result

## Question
Can an unprivileged attacker submit a hex-cased vs lower-cased signature value to `deriveSHA256HMACKey` in `crypto/utils.ts` such that a non-boolean return from deriveSHA256HMACKey is treated as success for a hex-cased vs lower-cased signature value, breaking the invariant that validator returns strict boolean and callers gate on it, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-api/runtime/crypto/utils.ts` -> `deriveSHA256HMACKey`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: a hex-cased vs lower-cased signature value
- Exploit idea: a non-boolean return from deriveSHA256HMACKey is treated as success for a hex-cased vs lower-cased signature value
- Invariant to test: validator returns strict boolean and callers gate on it
- Expected Immunefi impact: Auth bypass (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert callers reject non-true results
