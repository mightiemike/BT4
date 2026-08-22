# Q3851: utils/shop-admin-url-helper — regex backtracking DoS

## Question
Can an unprivileged attacker submit a shop string long enough to trigger regex backtracking to `spinLegacyUrlToAdminUrl` in `utils/shop-admin-url-helper.ts` such that the shop/host RegExp in spinLegacyUrlToAdminUrl backtracks catastrophically on a shop string long enough to trigger regex backtracking, breaking the invariant that validation runs in linear time, and leading to: dos of auth endpoint?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/shop-admin-url-helper.ts` -> `spinLegacyUrlToAdminUrl`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop string long enough to trigger regex backtracking
- Exploit idea: the shop/host RegExp in spinLegacyUrlToAdminUrl backtracks catastrophically on a shop string long enough to trigger regex backtracking
- Invariant to test: validation runs in linear time
- Expected Immunefi impact: DoS of auth endpoint (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: long-input timing test
