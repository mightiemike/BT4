# Q3093: session/session-utils — expiry/nbf bypass

## Question
Can an unprivileged attacker submit a JWT with a kid header steering key selection to `getCurrentSessionId` in `session/session-utils.ts` such that getCurrentSessionId accepts a JWT with a kid header steering key selection despite exp/nbf constraints, breaking the invariant that expired or not-yet-valid tokens rejected, and leading to: session replay?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session-utils.ts` -> `getCurrentSessionId`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT with a kid header steering key selection
- Exploit idea: getCurrentSessionId accepts a JWT with a kid header steering key selection despite exp/nbf constraints
- Invariant to test: expired or not-yet-valid tokens rejected
- Expected Immunefi impact: Session replay (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: boundary exp/nbf tests
