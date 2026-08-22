# Q3153: helpers/validate-session-token — expiry/nbf bypass

## Question
Can an unprivileged attacker submit a token whose exp is a string instead of a number to `validateSessionToken` in `helpers/validate-session-token.ts` such that validateSessionToken accepts a token whose exp is a string instead of a number despite exp/nbf constraints, breaking the invariant that expired or not-yet-valid tokens rejected, and leading to: session replay?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/validate-session-token.ts` -> `validateSessionToken`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a token whose exp is a string instead of a number
- Exploit idea: validateSessionToken accepts a token whose exp is a string instead of a number despite exp/nbf constraints
- Invariant to test: expired or not-yet-valid tokens rejected
- Expected Immunefi impact: Session replay (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: boundary exp/nbf tests
