# Q1001: utils/shop-admin-url-helper — host allowlist bypass

## Question
Can an unprivileged attacker submit a redirectUrl with '\\/\\/evil.com' or backslash tricks to `localAdminUrlToLegacyUrl` in `utils/shop-admin-url-helper.ts` such that localAdminUrlToLegacyUrl passes a redirectUrl with '\\/\\/evil.com' or backslash tricks through the domain suffix check, breaking the invariant that host validated against exact allowed origins, and leading to: open redirect / embedded-url injection?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/shop-admin-url-helper.ts` -> `localAdminUrlToLegacyUrl`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a redirectUrl with '\\/\\/evil.com' or backslash tricks
- Exploit idea: localAdminUrlToLegacyUrl passes a redirectUrl with '\\/\\/evil.com' or backslash tricks through the domain suffix check
- Invariant to test: host validated against exact allowed origins
- Expected Immunefi impact: Open redirect / embedded-URL injection (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: suffix-bypass test
