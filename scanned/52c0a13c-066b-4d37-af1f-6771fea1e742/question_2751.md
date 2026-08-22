# Q2751: session/session-utils — expiry/nbf bypass

## Question
Can an unprivileged attacker submit a bearer token placed in the URL param instead of the header to `getJwtSessionId` in `session/session-utils.ts` such that getJwtSessionId accepts a bearer token placed in the URL param instead of the header despite exp/nbf constraints, breaking the invariant that expired or not-yet-valid tokens rejected, and leading to: session replay?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session-utils.ts` -> `getJwtSessionId`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a bearer token placed in the URL param instead of the header
- Exploit idea: getJwtSessionId accepts a bearer token placed in the URL param instead of the header despite exp/nbf constraints
- Invariant to test: expired or not-yet-valid tokens rejected
- Expected Immunefi impact: Session replay (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: boundary exp/nbf tests
