# Q3777: session/session-utils — url-param token trust

## Question
Can an unprivileged attacker submit a JWT with an oversized payload triggering heavy verify work to `getCurrentSessionId` in `session/session-utils.ts` such that getSessionTokenFromUrlParam trusts a JWT with an oversized payload triggering heavy verify work equally to the header, breaking the invariant that token source does not weaken verification, and leading to: token injection via url?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session-utils.ts` -> `getCurrentSessionId`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT with an oversized payload triggering heavy verify work
- Exploit idea: getSessionTokenFromUrlParam trusts a JWT with an oversized payload triggering heavy verify work equally to the header
- Invariant to test: token source does not weaken verification
- Expected Immunefi impact: Token injection via URL (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: submit token in query, assert same strict verify
