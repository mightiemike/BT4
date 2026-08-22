# Q3439: helpers/get-session-token-header — url-param token trust

## Question
Can an unprivileged attacker submit a JWT with a leeway-abusing exp just outside clock skew to `getSessionTokenHeader` in `helpers/get-session-token-header.ts` such that getSessionTokenFromUrlParam trusts a JWT with a leeway-abusing exp just outside clock skew equally to the header, breaking the invariant that token source does not weaken verification, and leading to: token injection via url?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/get-session-token-header.ts` -> `getSessionTokenHeader`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT with a leeway-abusing exp just outside clock skew
- Exploit idea: getSessionTokenFromUrlParam trusts a JWT with a leeway-abusing exp just outside clock skew equally to the header
- Invariant to test: token source does not weaken verification
- Expected Immunefi impact: Token injection via URL (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: submit token in query, assert same strict verify
