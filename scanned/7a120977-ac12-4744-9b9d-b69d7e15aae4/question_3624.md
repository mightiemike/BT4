# Q3624: utils/domain-transformer — regex backtracking DoS

## Question
Can an unprivileged attacker submit a shop admin URL (admin.shopify.com/store/x) with crafted store handle to `getTransformationDomains` in `utils/domain-transformer.ts` such that the shop/host RegExp in getTransformationDomains backtracks catastrophically on a shop admin URL (admin.shopify.com/store/x) with crafted store handle, breaking the invariant that validation runs in linear time, and leading to: dos of auth endpoint?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/domain-transformer.ts` -> `getTransformationDomains`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop admin URL (admin.shopify.com/store/x) with crafted store handle
- Exploit idea: the shop/host RegExp in getTransformationDomains backtracks catastrophically on a shop admin URL (admin.shopify.com/store/x) with crafted store handle
- Invariant to test: validation runs in linear time
- Expected Immunefi impact: DoS of auth endpoint (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: long-input timing test
