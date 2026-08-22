# Q5017: utils/hmac-validator — truthy-coercion of result

## Question
Can an unprivileged attacker submit an empty or missing signature field to `validateHmacFromRequest` in `utils/hmac-validator.ts` such that a non-boolean return from validateHmacFromRequest is treated as success for an empty or missing signature field, breaking the invariant that validator returns strict boolean and callers gate on it, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/hmac-validator.ts` -> `validateHmacFromRequest`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: an empty or missing signature field
- Exploit idea: a non-boolean return from validateHmacFromRequest is treated as success for an empty or missing signature field
- Invariant to test: validator returns strict boolean and callers gate on it
- Expected Immunefi impact: Auth bypass (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert callers reject non-true results
