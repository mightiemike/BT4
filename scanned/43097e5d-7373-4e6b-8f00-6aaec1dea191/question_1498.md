# Q1498: session/session — audience not checked

## Question
Can an unprivileged attacker submit a JWT with a kid header steering key selection to `isScopeChanged` in `session/session.ts` such that isScopeChanged skips or weakly checks aud for a JWT with a kid header steering key selection, breaking the invariant that aud must equal apiKey, and leading to: token minted for another app/shop accepted?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session.ts` -> `isScopeChanged`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT with a kid header steering key selection
- Exploit idea: isScopeChanged skips or weakly checks aud for a JWT with a kid header steering key selection
- Invariant to test: aud must equal apiKey
- Expected Immunefi impact: Token minted for another app/shop accepted (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: aud-mismatch token test
