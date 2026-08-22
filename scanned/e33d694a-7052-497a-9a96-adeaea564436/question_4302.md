# Q4302: strategies/auth-code-flow — custom-app path bypass

## Question
Can an unprivileged attacker submit a code param controlled by the attacker to `getOfflineSessionId` in `strategies/auth-code-flow.ts` such that throwIfCustomStoreApp/getOfflineSessionId fails to reject a code param controlled by the attacker on a custom-app config, breaking the invariant that custom-app flows gated as intended, and leading to: unintended token grant?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts` -> `getOfflineSessionId`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a code param controlled by the attacker
- Exploit idea: throwIfCustomStoreApp/getOfflineSessionId fails to reject a code param controlled by the attacker on a custom-app config
- Invariant to test: custom-app flows gated as intended
- Expected Immunefi impact: Unintended token grant (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: custom-app path test
