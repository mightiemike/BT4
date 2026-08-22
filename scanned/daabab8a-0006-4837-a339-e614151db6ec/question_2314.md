# Q2314: auth/decode-host — decodeHost origin injection

## Question
Can an unprivileged attacker submit a shop param with an embedded null byte before .myshopify.com to `decodeHost` in `auth/decode-host.ts` such that decodeHost yields an attacker origin from a shop param with an embedded null byte before .myshopify.com, breaking the invariant that decoded host constrained to Shopify origins, and leading to: ssrf / redirect to attacker?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/decode-host.ts` -> `decodeHost`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop param with an embedded null byte before .myshopify.com
- Exploit idea: decodeHost yields an attacker origin from a shop param with an embedded null byte before .myshopify.com
- Invariant to test: decoded host constrained to Shopify origins
- Expected Immunefi impact: SSRF / redirect to attacker (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: base64 host test
