# Q3969: helpers/validate-redirect-url — regex backtracking DoS

## Question
Can an unprivileged attacker submit a host that decodes to a userinfo@ origin to `isSafe` in `helpers/validate-redirect-url.ts` such that the shop/host RegExp in isSafe backtracks catastrophically on a host that decodes to a userinfo@ origin, breaking the invariant that validation runs in linear time, and leading to: dos of auth endpoint?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts` -> `isSafe`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host that decodes to a userinfo@ origin
- Exploit idea: the shop/host RegExp in isSafe backtracks catastrophically on a host that decodes to a userinfo@ origin
- Invariant to test: validation runs in linear time
- Expected Immunefi impact: DoS of auth endpoint (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: long-input timing test
