# Q3136: utils/hmac-validator — query normalization mismatch

## Question
Can an unprivileged attacker submit a request where the hmac param itself is included in the signed set to `stringifyQueryForAdmin` in `utils/hmac-validator.ts` such that normalizeQuery/stringify* orders or encodes params so an unsigned variant validates, breaking the invariant that query serialization is injective and matches signer, and leading to: hmac bypass on app-proxy/callback?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/hmac-validator.ts` -> `stringifyQueryForAdmin`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: a request where the hmac param itself is included in the signed set
- Exploit idea: normalizeQuery/stringify* orders or encodes params so an unsigned variant validates
- Invariant to test: query serialization is injective and matches signer
- Expected Immunefi impact: HMAC bypass on app-proxy/callback (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: fuzz duplicated/reordered keys and assert single valid encoding
