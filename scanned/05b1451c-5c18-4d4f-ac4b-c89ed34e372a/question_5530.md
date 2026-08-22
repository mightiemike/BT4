# Q5530: utils/hmac-validator — truthy-coercion of result

## Question
Can an unprivileged attacker submit a request where the hmac param itself is included in the signed set to `normalizeQuery` in `utils/hmac-validator.ts` such that a non-boolean return from normalizeQuery is treated as success for a request where the hmac param itself is included in the signed set, breaking the invariant that validator returns strict boolean and callers gate on it, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/hmac-validator.ts` -> `normalizeQuery`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: a request where the hmac param itself is included in the signed set
- Exploit idea: a non-boolean return from normalizeQuery is treated as success for a request where the hmac param itself is included in the signed set
- Invariant to test: validator returns strict boolean and callers gate on it
- Expected Immunefi impact: Auth bypass (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert callers reject non-true results
