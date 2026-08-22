# Q1157: session/classes — audience not checked

## Question
Can an unprivileged attacker submit a bearer token placed in the URL param instead of the header to this module in `session/classes.ts` such that <module> skips or weakly checks aud for a bearer token placed in the URL param instead of the header, breaking the invariant that aud must equal apiKey, and leading to: token minted for another app/shop accepted?

## Target
- File/function: `packages/apps/shopify-api/lib/session/classes.ts` -> (module scope)
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a bearer token placed in the URL param instead of the header
- Exploit idea: <module> skips or weakly checks aud for a bearer token placed in the URL param instead of the header
- Invariant to test: aud must equal apiKey
- Expected Immunefi impact: Token minted for another app/shop accepted (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: aud-mismatch token test
