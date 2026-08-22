# Q3653: crypto/utils — key derivation confusion

## Question
Can an unprivileged attacker submit a Unicode-normalized copy of a signed value to `deriveSHA256HMACKey` in `crypto/utils.ts` such that getHMACKey/deriveSHA256HMACKey derives the wrong key material for a Unicode-normalized copy of a signed value, breaking the invariant that HMAC key equals app secret with correct algorithm, and leading to: universal signature forgery?

## Target
- File/function: `packages/apps/shopify-api/runtime/crypto/utils.ts` -> `deriveSHA256HMACKey`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: a Unicode-normalized copy of a signed value
- Exploit idea: getHMACKey/deriveSHA256HMACKey derives the wrong key material for a Unicode-normalized copy of a signed value
- Invariant to test: HMAC key equals app secret with correct algorithm
- Expected Immunefi impact: Universal signature forgery (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert derived key bytes match apiSecretKey
