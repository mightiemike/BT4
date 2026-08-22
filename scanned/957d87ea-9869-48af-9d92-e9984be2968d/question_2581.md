# Q2581: session/session — expiry/nbf bypass

## Question
Can an unprivileged attacker submit an expired JWT (exp in the past) or one with nbf in the future to `toObject` in `session/session.ts` such that toObject accepts an expired JWT (exp in the past) or one with nbf in the future despite exp/nbf constraints, breaking the invariant that expired or not-yet-valid tokens rejected, and leading to: session replay?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session.ts` -> `toObject`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: an expired JWT (exp in the past) or one with nbf in the future
- Exploit idea: toObject accepts an expired JWT (exp in the past) or one with nbf in the future despite exp/nbf constraints
- Invariant to test: expired or not-yet-valid tokens rejected
- Expected Immunefi impact: Session replay (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: boundary exp/nbf tests
