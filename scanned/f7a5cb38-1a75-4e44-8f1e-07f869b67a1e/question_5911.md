# Q5911: auth/decode-host — embedded-url injection

## Question
Can an unprivileged attacker submit a redirect target that is protocol-relative (//evil.com) to `decodeHost` in `auth/decode-host.ts` such that buildEmbeddedAppUrl/getEmbeddedAppUrl embeds a redirect target that is protocol-relative (//evil.com) unsanitized, breaking the invariant that embedded app URL host is verified, and leading to: xss/redirect in embedded frame?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/decode-host.ts` -> `decodeHost`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a redirect target that is protocol-relative (//evil.com)
- Exploit idea: buildEmbeddedAppUrl/getEmbeddedAppUrl embeds a redirect target that is protocol-relative (//evil.com) unsanitized
- Invariant to test: embedded app URL host is verified
- Expected Immunefi impact: XSS/redirect in embedded frame (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: host-injection test
