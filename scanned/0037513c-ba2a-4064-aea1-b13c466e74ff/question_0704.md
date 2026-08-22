# Q0704: helpers/get-session-token — alg/none acceptance

## Question
Can an unprivileged attacker submit a JWT with a kid header steering key selection to `getSessionTokenHeader` in `helpers/get-session-token.ts` such that getSessionTokenHeader accepts a JWT with a kid header steering key selection without enforcing HS256 against the app secret, breaking the invariant that JWT verified with expected alg and secret, and leading to: forge authenticated admin session for any shop?

## Target
- File/function: `packages/apps/shopify-app-express/src/helpers/get-session-token.ts` -> `getSessionTokenHeader`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT with a kid header steering key selection
- Exploit idea: getSessionTokenHeader accepts a JWT with a kid header steering key selection without enforcing HS256 against the app secret
- Invariant to test: JWT verified with expected alg and secret
- Expected Immunefi impact: Forge authenticated admin session for any shop (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forge alg=none token, expect verify failure
