# Q3281: utils/shop-admin-url-helper — regex backtracking DoS

## Question
Can an unprivileged attacker submit a shop value using uppercase/IDN/punycode to slip the domain regex to `legacyUrlToShopAdminUrl` in `utils/shop-admin-url-helper.ts` such that the shop/host RegExp in legacyUrlToShopAdminUrl backtracks catastrophically on a shop value using uppercase/IDN/punycode to slip the domain regex, breaking the invariant that validation runs in linear time, and leading to: dos of auth endpoint?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/shop-admin-url-helper.ts` -> `legacyUrlToShopAdminUrl`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop value using uppercase/IDN/punycode to slip the domain regex
- Exploit idea: the shop/host RegExp in legacyUrlToShopAdminUrl backtracks catastrophically on a shop value using uppercase/IDN/punycode to slip the domain regex
- Invariant to test: validation runs in linear time
- Expected Immunefi impact: DoS of auth endpoint (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: long-input timing test
