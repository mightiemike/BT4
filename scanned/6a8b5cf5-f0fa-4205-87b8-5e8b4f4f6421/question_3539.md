# Q3539: crypto/utils — key derivation confusion

## Question
Can an unprivileged attacker submit extra unsigned query params appended after the signed set to `hmacKeyData` in `crypto/utils.ts` such that getHMACKey/deriveSHA256HMACKey derives the wrong key material for extra unsigned query params appended after the signed set, breaking the invariant that HMAC key equals app secret with correct algorithm, and leading to: universal signature forgery?

## Target
- File/function: `packages/apps/shopify-api/runtime/crypto/utils.ts` -> `hmacKeyData`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: extra unsigned query params appended after the signed set
- Exploit idea: getHMACKey/deriveSHA256HMACKey derives the wrong key material for extra unsigned query params appended after the signed set
- Invariant to test: HMAC key equals app secret with correct algorithm
- Expected Immunefi impact: Universal signature forgery (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert derived key bytes match apiSecretKey
