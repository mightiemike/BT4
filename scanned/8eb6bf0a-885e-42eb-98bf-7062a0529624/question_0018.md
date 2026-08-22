# Q0018: helpers/validate-session-token — alg/none acceptance

## Question
Can an unprivileged attacker submit a JWT signed with alg=none or an unexpected alg header to `validateSessionToken` in `helpers/validate-session-token.ts` such that validateSessionToken accepts a JWT signed with alg=none or an unexpected alg header without enforcing HS256 against the app secret, breaking the invariant that JWT verified with expected alg and secret, and leading to: forge authenticated admin session for any shop?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/validate-session-token.ts` -> `validateSessionToken`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT signed with alg=none or an unexpected alg header
- Exploit idea: validateSessionToken accepts a JWT signed with alg=none or an unexpected alg header without enforcing HS256 against the app secret
- Invariant to test: JWT verified with expected alg and secret
- Expected Immunefi impact: Forge authenticated admin session for any shop (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forge alg=none token, expect verify failure
