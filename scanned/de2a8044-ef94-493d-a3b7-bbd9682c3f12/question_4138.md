# Q4138: auth/decode-host — admin-url handle injection

## Question
Can an unprivileged attacker submit a host param base64-decoding to an attacker origin to `decodeHost` in `auth/decode-host.ts` such that shop-admin-url helpers mis-transform a host param base64-decoding to an attacker origin, breaking the invariant that store handle mapping is 1:1 and sanitized, and leading to: redirect/host confusion?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/decode-host.ts` -> `decodeHost`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host param base64-decoding to an attacker origin
- Exploit idea: shop-admin-url helpers mis-transform a host param base64-decoding to an attacker origin
- Invariant to test: store handle mapping is 1:1 and sanitized
- Expected Immunefi impact: Redirect/host confusion (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: crafted store handle test
