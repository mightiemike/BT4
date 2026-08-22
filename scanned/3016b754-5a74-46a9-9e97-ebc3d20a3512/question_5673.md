# Q5673: auth/decode-host — embedded-url injection

## Question
Can an unprivileged attacker submit a host param base64-decoding to an attacker origin to `decodeHost` in `auth/decode-host.ts` such that buildEmbeddedAppUrl/getEmbeddedAppUrl embeds a host param base64-decoding to an attacker origin unsanitized, breaking the invariant that embedded app URL host is verified, and leading to: xss/redirect in embedded frame?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/decode-host.ts` -> `decodeHost`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host param base64-decoding to an attacker origin
- Exploit idea: buildEmbeddedAppUrl/getEmbeddedAppUrl embeds a host param base64-decoding to an attacker origin unsanitized
- Invariant to test: embedded app URL host is verified
- Expected Immunefi impact: XSS/redirect in embedded frame (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: host-injection test
