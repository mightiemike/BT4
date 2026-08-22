# Q2865: session/session-utils — expiry/nbf bypass

## Question
Can an unprivileged attacker submit a token whose sub encodes another user's id to `getCurrentSessionId` in `session/session-utils.ts` such that getCurrentSessionId accepts a token whose sub encodes another user's id despite exp/nbf constraints, breaking the invariant that expired or not-yet-valid tokens rejected, and leading to: session replay?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session-utils.ts` -> `getCurrentSessionId`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a token whose sub encodes another user's id
- Exploit idea: getCurrentSessionId accepts a token whose sub encodes another user's id despite exp/nbf constraints
- Invariant to test: expired or not-yet-valid tokens rejected
- Expected Immunefi impact: Session replay (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: boundary exp/nbf tests
