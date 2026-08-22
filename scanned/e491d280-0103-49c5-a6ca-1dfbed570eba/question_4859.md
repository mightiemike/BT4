# Q4859: session/decode-session-token — claim-shape confusion

## Question
Can an unprivileged attacker submit a JWT whose aud does not equal the app apiKey to `decodeSessionToken` in `session/decode-session-token.ts` such that decodeSessionToken mishandles array/duplicate claims in a JWT whose aud does not equal the app apiKey, breaking the invariant that single scalar claim enforced, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-api/lib/session/decode-session-token.ts` -> `decodeSessionToken`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT whose aud does not equal the app apiKey
- Exploit idea: decodeSessionToken mishandles array/duplicate claims in a JWT whose aud does not equal the app apiKey
- Invariant to test: single scalar claim enforced
- Expected Immunefi impact: Auth bypass (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: duplicate-claim token test
