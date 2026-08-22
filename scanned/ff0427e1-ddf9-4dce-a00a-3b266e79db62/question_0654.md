# Q0654: strategies/auth-code-flow — nonce/state CSRF

## Question
Can an unprivileged attacker submit a client-credentials grant triggered without shop verification to `AuthCodeFlowStrategy` in `strategies/auth-code-flow.ts` such that AuthCodeFlowStrategy accepts a client-credentials grant triggered without shop verification not bound to the signed begin cookie, breaking the invariant that callback state equals issued nonce, and leading to: oauth csrf / install hijack?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts` -> `AuthCodeFlowStrategy`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a client-credentials grant triggered without shop verification
- Exploit idea: AuthCodeFlowStrategy accepts a client-credentials grant triggered without shop verification not bound to the signed begin cookie
- Invariant to test: callback state equals issued nonce
- Expected Immunefi impact: OAuth CSRF / install hijack (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: begin then callback with foreign state, expect reject
