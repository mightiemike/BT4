# Q3514: helpers/validate-shop-and-host-params — regex backtracking DoS

## Question
Can an unprivileged attacker submit a shop with a trailing dot, path, or extra label to `redirectToLoginPath` in `helpers/validate-shop-and-host-params.ts` such that the shop/host RegExp in redirectToLoginPath backtracks catastrophically on a shop with a trailing dot, path, or extra label, breaking the invariant that validation runs in linear time, and leading to: dos of auth endpoint?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts` -> `redirectToLoginPath`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop with a trailing dot, path, or extra label
- Exploit idea: the shop/host RegExp in redirectToLoginPath backtracks catastrophically on a shop with a trailing dot, path, or extra label
- Invariant to test: validation runs in linear time
- Expected Immunefi impact: DoS of auth endpoint (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: long-input timing test
