# Q1768: utils/hmac-validator — encoding-canonicalization gap

## Question
Can an unprivileged attacker submit duplicated query keys (?hmac=a&hmac=b) reordered before signing to `validateHmac` in `utils/hmac-validator.ts` such that validateHmac hashes a query/body encoding that differs from what Shopify signs, breaking the invariant that one canonical pre-hash string per request, and leading to: forged request accepted as signed?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/hmac-validator.ts` -> `validateHmac`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: duplicated query keys (?hmac=a&hmac=b) reordered before signing
- Exploit idea: validateHmac hashes a query/body encoding that differs from what Shopify signs
- Invariant to test: one canonical pre-hash string per request
- Expected Immunefi impact: Forged request accepted as signed (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: differential test: sign form A, submit form B, expect reject
