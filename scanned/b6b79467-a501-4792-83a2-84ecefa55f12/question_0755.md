# Q0755: session/decode-session-token — alg/none acceptance

## Question
Can an unprivileged attacker submit a token whose exp is a string instead of a number to `decodeSessionToken` in `session/decode-session-token.ts` such that decodeSessionToken accepts a token whose exp is a string instead of a number without enforcing HS256 against the app secret, breaking the invariant that JWT verified with expected alg and secret, and leading to: forge authenticated admin session for any shop?

## Target
- File/function: `packages/apps/shopify-api/lib/session/decode-session-token.ts` -> `decodeSessionToken`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a token whose exp is a string instead of a number
- Exploit idea: decodeSessionToken accepts a token whose exp is a string instead of a number without enforcing HS256 against the app secret
- Invariant to test: JWT verified with expected alg and secret
- Expected Immunefi impact: Forge authenticated admin session for any shop (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forge alg=none token, expect verify failure
