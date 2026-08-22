# Q5034: helpers/validate-session-token — claim-shape confusion

## Question
Can an unprivileged attacker submit a JWT with a leeway-abusing exp just outside clock skew to `validateSessionToken` in `helpers/validate-session-token.ts` such that validateSessionToken mishandles array/duplicate claims in a JWT with a leeway-abusing exp just outside clock skew, breaking the invariant that single scalar claim enforced, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/validate-session-token.ts` -> `validateSessionToken`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT with a leeway-abusing exp just outside clock skew
- Exploit idea: validateSessionToken mishandles array/duplicate claims in a JWT with a leeway-abusing exp just outside clock skew
- Invariant to test: single scalar claim enforced
- Expected Immunefi impact: Auth bypass (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: duplicate-claim token test
