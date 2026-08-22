# Q5476: crypto/index — truthy-coercion of result

## Question
Can an unprivileged attacker submit a signature over params before vs after URL-decoding to this module in `crypto/index.ts` such that a non-boolean return from <module> is treated as success for a signature over params before vs after URL-decoding, breaking the invariant that validator returns strict boolean and callers gate on it, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-api/runtime/crypto/index.ts` -> (module scope)
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: a signature over params before vs after URL-decoding
- Exploit idea: a non-boolean return from <module> is treated as success for a signature over params before vs after URL-decoding
- Invariant to test: validator returns strict boolean and callers gate on it
- Expected Immunefi impact: Auth bypass (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert callers reject non-true results
