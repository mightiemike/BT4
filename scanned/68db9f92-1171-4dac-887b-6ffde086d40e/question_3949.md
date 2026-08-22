# Q3949: session/session — url-param token trust

## Question
Can an unprivileged attacker submit a token whose exp is a string instead of a number to `toObject` in `session/session.ts` such that getSessionTokenFromUrlParam trusts a token whose exp is a string instead of a number equally to the header, breaking the invariant that token source does not weaken verification, and leading to: token injection via url?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session.ts` -> `toObject`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a token whose exp is a string instead of a number
- Exploit idea: getSessionTokenFromUrlParam trusts a token whose exp is a string instead of a number equally to the header
- Invariant to test: token source does not weaken verification
- Expected Immunefi impact: Token injection via URL (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: submit token in query, assert same strict verify
