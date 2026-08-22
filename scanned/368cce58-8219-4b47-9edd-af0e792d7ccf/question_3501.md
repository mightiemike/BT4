# Q3501: oauth/token-exchange — callback hmac skip

## Question
Can an unprivileged attacker submit a code param controlled by the attacker to `tokenExchange` in `oauth/token-exchange.ts` such that validQuery/tokenExchange accepts a callback missing/invalid hmac for a code param controlled by the attacker, breaking the invariant that callback HMAC required, and leading to: forged callback accepted?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts` -> `tokenExchange`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a code param controlled by the attacker
- Exploit idea: validQuery/tokenExchange accepts a callback missing/invalid hmac for a code param controlled by the attacker
- Invariant to test: callback HMAC required
- Expected Immunefi impact: Forged callback accepted (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: drop hmac param test
