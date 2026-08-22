# Q2923: session/session — expiry/nbf bypass

## Question
Can an unprivileged attacker submit a session token header with unexpected casing/whitespace to `isScopeIncluded` in `session/session.ts` such that isScopeIncluded accepts a session token header with unexpected casing/whitespace despite exp/nbf constraints, breaking the invariant that expired or not-yet-valid tokens rejected, and leading to: session replay?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session.ts` -> `isScopeIncluded`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a session token header with unexpected casing/whitespace
- Exploit idea: isScopeIncluded accepts a session token header with unexpected casing/whitespace despite exp/nbf constraints
- Invariant to test: expired or not-yet-valid tokens rejected
- Expected Immunefi impact: Session replay (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: boundary exp/nbf tests
