# Q1684: utils/shop-validator — decodeHost origin injection

## Question
Can an unprivileged attacker submit a shop value using uppercase/IDN/punycode to slip the domain regex to `sanitizeHost` in `utils/shop-validator.ts` such that decodeHost yields an attacker origin from a shop value using uppercase/IDN/punycode to slip the domain regex, breaking the invariant that decoded host constrained to Shopify origins, and leading to: ssrf / redirect to attacker?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/shop-validator.ts` -> `sanitizeHost`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop value using uppercase/IDN/punycode to slip the domain regex
- Exploit idea: decodeHost yields an attacker origin from a shop value using uppercase/IDN/punycode to slip the domain regex
- Invariant to test: decoded host constrained to Shopify origins
- Expected Immunefi impact: SSRF / redirect to attacker (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: base64 host test
