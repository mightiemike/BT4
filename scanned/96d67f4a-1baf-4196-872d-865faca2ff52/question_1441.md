# Q1441: session/session — audience not checked

## Question
Can an unprivileged attacker submit a token whose iss host differs from dest host to `isActive` in `session/session.ts` such that isActive skips or weakly checks aud for a token whose iss host differs from dest host, breaking the invariant that aud must equal apiKey, and leading to: token minted for another app/shop accepted?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session.ts` -> `isActive`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a token whose iss host differs from dest host
- Exploit idea: isActive skips or weakly checks aud for a token whose iss host differs from dest host
- Invariant to test: aud must equal apiKey
- Expected Immunefi impact: Token minted for another app/shop accepted (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: aud-mismatch token test
