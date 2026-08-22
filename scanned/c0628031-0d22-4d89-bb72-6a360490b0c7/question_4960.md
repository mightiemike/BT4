# Q4960: utils/hmac-validator — truthy-coercion of result

## Question
Can an unprivileged attacker submit duplicated query keys (?hmac=a&hmac=b) reordered before signing to `normalizeQuery` in `utils/hmac-validator.ts` such that a non-boolean return from normalizeQuery is treated as success for duplicated query keys (?hmac=a&hmac=b) reordered before signing, breaking the invariant that validator returns strict boolean and callers gate on it, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/hmac-validator.ts` -> `normalizeQuery`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: duplicated query keys (?hmac=a&hmac=b) reordered before signing
- Exploit idea: a non-boolean return from normalizeQuery is treated as success for duplicated query keys (?hmac=a&hmac=b) reordered before signing
- Invariant to test: validator returns strict boolean and callers gate on it
- Expected Immunefi impact: Auth bypass (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert callers reject non-true results
