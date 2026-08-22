# Q2978: session/decode-session-token — expiry/nbf bypass

## Question
Can an unprivileged attacker submit a JWT with an oversized payload triggering heavy verify work to `decodeSessionToken` in `session/decode-session-token.ts` such that decodeSessionToken accepts a JWT with an oversized payload triggering heavy verify work despite exp/nbf constraints, breaking the invariant that expired or not-yet-valid tokens rejected, and leading to: session replay?

## Target
- File/function: `packages/apps/shopify-api/lib/session/decode-session-token.ts` -> `decodeSessionToken`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT with an oversized payload triggering heavy verify work
- Exploit idea: decodeSessionToken accepts a JWT with an oversized payload triggering heavy verify work despite exp/nbf constraints
- Invariant to test: expired or not-yet-valid tokens rejected
- Expected Immunefi impact: Session replay (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: boundary exp/nbf tests
