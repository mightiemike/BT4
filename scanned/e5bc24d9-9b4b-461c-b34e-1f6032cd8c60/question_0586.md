# Q0586: session/session — alg/none acceptance

## Question
Can an unprivileged attacker submit a JWT with an oversized payload triggering heavy verify work to `isScopeChanged` in `session/session.ts` such that isScopeChanged accepts a JWT with an oversized payload triggering heavy verify work without enforcing HS256 against the app secret, breaking the invariant that JWT verified with expected alg and secret, and leading to: forge authenticated admin session for any shop?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session.ts` -> `isScopeChanged`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT with an oversized payload triggering heavy verify work
- Exploit idea: isScopeChanged accepts a JWT with an oversized payload triggering heavy verify work without enforcing HS256 against the app secret
- Invariant to test: JWT verified with expected alg and secret
- Expected Immunefi impact: Forge authenticated admin session for any shop (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forge alg=none token, expect verify failure
