# Q4847: utils/get-hmac-key — truthy-coercion of result

## Question
Can an unprivileged attacker submit a hex-cased vs lower-cased signature value to `getHMACKey` in `utils/get-hmac-key.ts` such that a non-boolean return from getHMACKey is treated as success for a hex-cased vs lower-cased signature value, breaking the invariant that validator returns strict boolean and callers gate on it, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/get-hmac-key.ts` -> `getHMACKey`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: a hex-cased vs lower-cased signature value
- Exploit idea: a non-boolean return from getHMACKey is treated as success for a hex-cased vs lower-cased signature value
- Invariant to test: validator returns strict boolean and callers gate on it
- Expected Immunefi impact: Auth bypass (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert callers reject non-true results
