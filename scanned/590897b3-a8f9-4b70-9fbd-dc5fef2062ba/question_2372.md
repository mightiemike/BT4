# Q2372: auth/get-embedded-app-url — decodeHost origin injection

## Question
Can an unprivileged attacker submit a host that decodes to a userinfo@ origin to `buildEmbeddedAppUrl` in `auth/get-embedded-app-url.ts` such that decodeHost yields an attacker origin from a host that decodes to a userinfo@ origin, breaking the invariant that decoded host constrained to Shopify origins, and leading to: ssrf / redirect to attacker?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/get-embedded-app-url.ts` -> `buildEmbeddedAppUrl`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host that decodes to a userinfo@ origin
- Exploit idea: decodeHost yields an attacker origin from a host that decodes to a userinfo@ origin
- Invariant to test: decoded host constrained to Shopify origins
- Expected Immunefi impact: SSRF / redirect to attacker (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: base64 host test
