# Q2200: auth/decode-host — decodeHost origin injection

## Question
Can an unprivileged attacker submit a host param with CRLF to split headers on redirect to `decodeHost` in `auth/decode-host.ts` such that decodeHost yields an attacker origin from a host param with CRLF to split headers on redirect, breaking the invariant that decoded host constrained to Shopify origins, and leading to: ssrf / redirect to attacker?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/decode-host.ts` -> `decodeHost`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host param with CRLF to split headers on redirect
- Exploit idea: decodeHost yields an attacker origin from a host param with CRLF to split headers on redirect
- Invariant to test: decoded host constrained to Shopify origins
- Expected Immunefi impact: SSRF / redirect to attacker (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: base64 host test
