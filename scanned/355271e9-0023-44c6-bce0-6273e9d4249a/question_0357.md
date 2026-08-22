# Q0357: session/session-utils — alg/none acceptance

## Question
Can an unprivileged attacker submit a bearer token placed in the URL param instead of the header to `getCurrentSessionId` in `session/session-utils.ts` such that getCurrentSessionId accepts a bearer token placed in the URL param instead of the header without enforcing HS256 against the app secret, breaking the invariant that JWT verified with expected alg and secret, and leading to: forge authenticated admin session for any shop?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session-utils.ts` -> `getCurrentSessionId`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a bearer token placed in the URL param instead of the header
- Exploit idea: getCurrentSessionId accepts a bearer token placed in the URL param instead of the header without enforcing HS256 against the app secret
- Invariant to test: JWT verified with expected alg and secret
- Expected Immunefi impact: Forge authenticated admin session for any shop (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forge alg=none token, expect verify failure
