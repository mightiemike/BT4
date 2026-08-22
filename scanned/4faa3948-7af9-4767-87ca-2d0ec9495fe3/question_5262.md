# Q5262: helpers/validate-session-token — claim-shape confusion

## Question
Can an unprivileged attacker submit a token whose sub encodes another user's id to `validateSessionToken` in `helpers/validate-session-token.ts` such that validateSessionToken mishandles array/duplicate claims in a token whose sub encodes another user's id, breaking the invariant that single scalar claim enforced, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/validate-session-token.ts` -> `validateSessionToken`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a token whose sub encodes another user's id
- Exploit idea: validateSessionToken mishandles array/duplicate claims in a token whose sub encodes another user's id
- Invariant to test: single scalar claim enforced
- Expected Immunefi impact: Auth bypass (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: duplicate-claim token test
