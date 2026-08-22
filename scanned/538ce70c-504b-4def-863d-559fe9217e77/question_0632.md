# Q0632: crypto/utils — non-constant-time compare

## Question
Can an unprivileged attacker submit mixed encoding of '+' vs '%20' in the pre-hash string to `hashString` in `crypto/utils.ts` such that the digest comparison in hashString short-circuits on first mismatched byte, breaking the invariant that request authenticity via constant-time HMAC equality, and leading to: signature/hmac verification bypass leading to acceptance of forged shopify requests?

## Target
- File/function: `packages/apps/shopify-api/runtime/crypto/utils.ts` -> `hashString`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: mixed encoding of '+' vs '%20' in the pre-hash string
- Exploit idea: the digest comparison in hashString short-circuits on first mismatched byte
- Invariant to test: request authenticity via constant-time HMAC equality
- Expected Immunefi impact: Signature/HMAC verification bypass leading to acceptance of forged Shopify requests (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: Jest: feed two signatures differing at byte 0 vs byte N and measure/inspect the compare path
