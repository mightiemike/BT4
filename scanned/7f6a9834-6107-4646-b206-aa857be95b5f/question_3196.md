# Q3196: crypto/index — key derivation confusion

## Question
Can an unprivileged attacker submit an HMAC header whose bytes differ from the expected digest only in length to this module in `crypto/index.ts` such that getHMACKey/deriveSHA256HMACKey derives the wrong key material for an HMAC header whose bytes differ from the expected digest only in length, breaking the invariant that HMAC key equals app secret with correct algorithm, and leading to: universal signature forgery?

## Target
- File/function: `packages/apps/shopify-api/runtime/crypto/index.ts` -> (module scope)
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: an HMAC header whose bytes differ from the expected digest only in length
- Exploit idea: getHMACKey/deriveSHA256HMACKey derives the wrong key material for an HMAC header whose bytes differ from the expected digest only in length
- Invariant to test: HMAC key equals app secret with correct algorithm
- Expected Immunefi impact: Universal signature forgery (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert derived key bytes match apiSecretKey
