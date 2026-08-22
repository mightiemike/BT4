# Q1382: session/decode-session-token — audience not checked

## Question
Can an unprivileged attacker submit a JWT with an oversized payload triggering heavy verify work to `decodeSessionToken` in `session/decode-session-token.ts` such that decodeSessionToken skips or weakly checks aud for a JWT with an oversized payload triggering heavy verify work, breaking the invariant that aud must equal apiKey, and leading to: token minted for another app/shop accepted?

## Target
- File/function: `packages/apps/shopify-api/lib/session/decode-session-token.ts` -> `decodeSessionToken`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT with an oversized payload triggering heavy verify work
- Exploit idea: decodeSessionToken skips or weakly checks aud for a JWT with an oversized payload triggering heavy verify work
- Invariant to test: aud must equal apiKey
- Expected Immunefi impact: Token minted for another app/shop accepted (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: aud-mismatch token test
