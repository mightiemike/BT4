# Q5089: session/session — claim-shape confusion

## Question
Can an unprivileged attacker submit a session id derived from an attacker-chosen dest claim to `isActive` in `session/session.ts` such that isActive mishandles array/duplicate claims in a session id derived from an attacker-chosen dest claim, breaking the invariant that single scalar claim enforced, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session.ts` -> `isActive`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a session id derived from an attacker-chosen dest claim
- Exploit idea: isActive mishandles array/duplicate claims in a session id derived from an attacker-chosen dest claim
- Invariant to test: single scalar claim enforced
- Expected Immunefi impact: Auth bypass (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: duplicate-claim token test
