# Q0073: session/session — alg/none acceptance

## Question
Can an unprivileged attacker submit a JWT whose aud does not equal the app apiKey to `isActive` in `session/session.ts` such that isActive accepts a JWT whose aud does not equal the app apiKey without enforcing HS256 against the app secret, breaking the invariant that JWT verified with expected alg and secret, and leading to: forge authenticated admin session for any shop?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session.ts` -> `isActive`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT whose aud does not equal the app apiKey
- Exploit idea: isActive accepts a JWT whose aud does not equal the app apiKey without enforcing HS256 against the app secret
- Invariant to test: JWT verified with expected alg and secret
- Expected Immunefi impact: Forge authenticated admin session for any shop (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forge alg=none token, expect verify failure
