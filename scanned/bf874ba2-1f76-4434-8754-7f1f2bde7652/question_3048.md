# Q3048: strategies/auth-code-flow — cookie forgery

## Question
Can an unprivileged attacker submit a client-credentials grant triggered without shop verification to `getOfflineSessionId` in `strategies/auth-code-flow.ts` such that the OAuth signed cookie is forgeable/settable via a client-credentials grant triggered without shop verification, breaking the invariant that cookie integrity via app secret, and leading to: session fixation / csrf?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts` -> `getOfflineSessionId`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a client-credentials grant triggered without shop verification
- Exploit idea: the OAuth signed cookie is forgeable/settable via a client-credentials grant triggered without shop verification
- Invariant to test: cookie integrity via app secret
- Expected Immunefi impact: Session fixation / CSRF (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forge cookie signature test
