# Q3383: helpers/get-session-token — url-param token trust

## Question
Can an unprivileged attacker submit an expired JWT (exp in the past) or one with nbf in the future to `getSessionToken` in `helpers/get-session-token.ts` such that getSessionTokenFromUrlParam trusts an expired JWT (exp in the past) or one with nbf in the future equally to the header, breaking the invariant that token source does not weaken verification, and leading to: token injection via url?

## Target
- File/function: `packages/apps/shopify-app-express/src/helpers/get-session-token.ts` -> `getSessionToken`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: an expired JWT (exp in the past) or one with nbf in the future
- Exploit idea: getSessionTokenFromUrlParam trusts an expired JWT (exp in the past) or one with nbf in the future equally to the header
- Invariant to test: token source does not weaken verification
- Expected Immunefi impact: Token injection via URL (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: submit token in query, assert same strict verify
