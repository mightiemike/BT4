# Q3478: utils/hmac-validator — key derivation confusion

## Question
Can an unprivileged attacker submit a signature computed over a differently-encoded query string to `validateHmac` in `utils/hmac-validator.ts` such that getHMACKey/deriveSHA256HMACKey derives the wrong key material for a signature computed over a differently-encoded query string, breaking the invariant that HMAC key equals app secret with correct algorithm, and leading to: universal signature forgery?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/hmac-validator.ts` -> `validateHmac`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: a signature computed over a differently-encoded query string
- Exploit idea: getHMACKey/deriveSHA256HMACKey derives the wrong key material for a signature computed over a differently-encoded query string
- Invariant to test: HMAC key equals app secret with correct algorithm
- Expected Immunefi impact: Universal signature forgery (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert derived key bytes match apiSecretKey
