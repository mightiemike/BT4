# Q0814: session/session — audience not checked

## Question
Can an unprivileged attacker submit a JWT signed with alg=none or an unexpected alg header to `equals` in `session/session.ts` such that equals skips or weakly checks aud for a JWT signed with alg=none or an unexpected alg header, breaking the invariant that aud must equal apiKey, and leading to: token minted for another app/shop accepted?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session.ts` -> `equals`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT signed with alg=none or an unexpected alg header
- Exploit idea: equals skips or weakly checks aud for a JWT signed with alg=none or an unexpected alg header
- Invariant to test: aud must equal apiKey
- Expected Immunefi impact: Token minted for another app/shop accepted (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: aud-mismatch token test
