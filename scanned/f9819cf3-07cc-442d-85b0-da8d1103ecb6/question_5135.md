# Q5135: crypto/utils — truthy-coercion of result

## Question
Can an unprivileged attacker submit extra unsigned query params appended after the signed set to `hmacKeyData` in `crypto/utils.ts` such that a non-boolean return from hmacKeyData is treated as success for extra unsigned query params appended after the signed set, breaking the invariant that validator returns strict boolean and callers gate on it, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-api/runtime/crypto/utils.ts` -> `hmacKeyData`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: extra unsigned query params appended after the signed set
- Exploit idea: a non-boolean return from hmacKeyData is treated as success for extra unsigned query params appended after the signed set
- Invariant to test: validator returns strict boolean and callers gate on it
- Expected Immunefi impact: Auth bypass (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert callers reject non-true results
