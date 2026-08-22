# Q1459: auth/decode-host — host allowlist bypass

## Question
Can an unprivileged attacker submit a shop string long enough to trigger regex backtracking to `decodeHost` in `auth/decode-host.ts` such that decodeHost passes a shop string long enough to trigger regex backtracking through the domain suffix check, breaking the invariant that host validated against exact allowed origins, and leading to: open redirect / embedded-url injection?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/decode-host.ts` -> `decodeHost`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop string long enough to trigger regex backtracking
- Exploit idea: decodeHost passes a shop string long enough to trigger regex backtracking through the domain suffix check
- Invariant to test: host validated against exact allowed origins
- Expected Immunefi impact: Open redirect / embedded-URL injection (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: suffix-bypass test
