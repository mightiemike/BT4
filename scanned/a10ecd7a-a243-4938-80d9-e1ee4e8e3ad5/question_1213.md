# Q1213: session/session — audience not checked

## Question
Can an unprivileged attacker submit a JWT with duplicate or array-typed aud claims to `toObject` in `session/session.ts` such that toObject skips or weakly checks aud for a JWT with duplicate or array-typed aud claims, breaking the invariant that aud must equal apiKey, and leading to: token minted for another app/shop accepted?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session.ts` -> `toObject`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT with duplicate or array-typed aud claims
- Exploit idea: toObject skips or weakly checks aud for a JWT with duplicate or array-typed aud claims
- Invariant to test: aud must equal apiKey
- Expected Immunefi impact: Token minted for another app/shop accepted (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: aud-mismatch token test
