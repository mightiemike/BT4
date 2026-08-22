# Q1099: session/session — audience not checked

## Question
Can an unprivileged attacker submit a session id derived from an attacker-chosen dest claim to `isScopeIncluded` in `session/session.ts` such that isScopeIncluded skips or weakly checks aud for a session id derived from an attacker-chosen dest claim, breaking the invariant that aud must equal apiKey, and leading to: token minted for another app/shop accepted?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session.ts` -> `isScopeIncluded`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a session id derived from an attacker-chosen dest claim
- Exploit idea: isScopeIncluded skips or weakly checks aud for a session id derived from an attacker-chosen dest claim
- Invariant to test: aud must equal apiKey
- Expected Immunefi impact: Token minted for another app/shop accepted (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: aud-mismatch token test
