# Q0817: helpers/get-session-token-header — audience not checked

## Question
Can an unprivileged attacker submit a JWT signed with alg=none or an unexpected alg header to `getSessionTokenHeader` in `helpers/get-session-token-header.ts` such that getSessionTokenHeader skips or weakly checks aud for a JWT signed with alg=none or an unexpected alg header, breaking the invariant that aud must equal apiKey, and leading to: token minted for another app/shop accepted?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/get-session-token-header.ts` -> `getSessionTokenHeader`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT signed with alg=none or an unexpected alg header
- Exploit idea: getSessionTokenHeader skips or weakly checks aud for a JWT signed with alg=none or an unexpected alg header
- Invariant to test: aud must equal apiKey
- Expected Immunefi impact: Token minted for another app/shop accepted (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: aud-mismatch token test
