# Q3625: auth/decode-host — regex backtracking DoS

## Question
Can an unprivileged attacker submit a shop admin URL (admin.shopify.com/store/x) with crafted store handle to `decodeHost` in `auth/decode-host.ts` such that the shop/host RegExp in decodeHost backtracks catastrophically on a shop admin URL (admin.shopify.com/store/x) with crafted store handle, breaking the invariant that validation runs in linear time, and leading to: dos of auth endpoint?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/decode-host.ts` -> `decodeHost`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop admin URL (admin.shopify.com/store/x) with crafted store handle
- Exploit idea: the shop/host RegExp in decodeHost backtracks catastrophically on a shop admin URL (admin.shopify.com/store/x) with crafted store handle
- Invariant to test: validation runs in linear time
- Expected Immunefi impact: DoS of auth endpoint (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: long-input timing test
