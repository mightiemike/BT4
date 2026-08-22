# Q5416: utils/hmac-validator — truthy-coercion of result

## Question
Can an unprivileged attacker submit mixed encoding of '+' vs '%20' in the pre-hash string to `stringifyQueryForAdmin` in `utils/hmac-validator.ts` such that a non-boolean return from stringifyQueryForAdmin is treated as success for mixed encoding of '+' vs '%20' in the pre-hash string, breaking the invariant that validator returns strict boolean and callers gate on it, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/hmac-validator.ts` -> `stringifyQueryForAdmin`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: mixed encoding of '+' vs '%20' in the pre-hash string
- Exploit idea: a non-boolean return from stringifyQueryForAdmin is treated as success for mixed encoding of '+' vs '%20' in the pre-hash string
- Invariant to test: validator returns strict boolean and callers gate on it
- Expected Immunefi impact: Auth bypass (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert callers reject non-true results
