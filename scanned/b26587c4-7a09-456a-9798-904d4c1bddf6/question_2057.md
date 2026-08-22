# Q2057: crypto/utils — encoding-canonicalization gap

## Question
Can an unprivileged attacker submit a Unicode-normalized copy of a signed value to `deriveSHA256HMACKey` in `crypto/utils.ts` such that deriveSHA256HMACKey hashes a query/body encoding that differs from what Shopify signs, breaking the invariant that one canonical pre-hash string per request, and leading to: forged request accepted as signed?

## Target
- File/function: `packages/apps/shopify-api/runtime/crypto/utils.ts` -> `deriveSHA256HMACKey`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: a Unicode-normalized copy of a signed value
- Exploit idea: deriveSHA256HMACKey hashes a query/body encoding that differs from what Shopify signs
- Invariant to test: one canonical pre-hash string per request
- Expected Immunefi impact: Forged request accepted as signed (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: differential test: sign form A, submit form B, expect reject
