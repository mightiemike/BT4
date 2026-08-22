# Q0189: helpers/validate-session-token — alg/none acceptance

## Question
Can an unprivileged attacker submit an expired JWT (exp in the past) or one with nbf in the future to `validateSessionToken` in `helpers/validate-session-token.ts` such that validateSessionToken accepts an expired JWT (exp in the past) or one with nbf in the future without enforcing HS256 against the app secret, breaking the invariant that JWT verified with expected alg and secret, and leading to: forge authenticated admin session for any shop?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/validate-session-token.ts` -> `validateSessionToken`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: an expired JWT (exp in the past) or one with nbf in the future
- Exploit idea: validateSessionToken accepts an expired JWT (exp in the past) or one with nbf in the future without enforcing HS256 against the app secret
- Invariant to test: JWT verified with expected alg and secret
- Expected Immunefi impact: Forge authenticated admin session for any shop (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forge alg=none token, expect verify failure
