# Q3267: helpers/validate-session-token — url-param token trust

## Question
Can an unprivileged attacker submit a JWT whose aud does not equal the app apiKey to `validateSessionToken` in `helpers/validate-session-token.ts` such that getSessionTokenFromUrlParam trusts a JWT whose aud does not equal the app apiKey equally to the header, breaking the invariant that token source does not weaken verification, and leading to: token injection via url?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/validate-session-token.ts` -> `validateSessionToken`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT whose aud does not equal the app apiKey
- Exploit idea: getSessionTokenFromUrlParam trusts a JWT whose aud does not equal the app apiKey equally to the header
- Invariant to test: token source does not weaken verification
- Expected Immunefi impact: Token injection via URL (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: submit token in query, assert same strict verify
