# Q2224: utils/hmac-validator — encoding-canonicalization gap

## Question
Can an unprivileged attacker submit mixed encoding of '+' vs '%20' in the pre-hash string to `validateHmacTimestamp` in `utils/hmac-validator.ts` such that validateHmacTimestamp hashes a query/body encoding that differs from what Shopify signs, breaking the invariant that one canonical pre-hash string per request, and leading to: forged request accepted as signed?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/hmac-validator.ts` -> `validateHmacTimestamp`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: mixed encoding of '+' vs '%20' in the pre-hash string
- Exploit idea: validateHmacTimestamp hashes a query/body encoding that differs from what Shopify signs
- Invariant to test: one canonical pre-hash string per request
- Expected Immunefi impact: Forged request accepted as signed (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: differential test: sign form A, submit form B, expect reject
