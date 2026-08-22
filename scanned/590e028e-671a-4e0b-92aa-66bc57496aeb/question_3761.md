# Q3761: src/validations — retry amplification

## Question
Can an unprivileged attacker submit a server-side usage guard bypassed from a browser context to `validateServerSideUsage` in `src/validations.ts` such that generateHttpFetch retries a server-side usage guard bypassed from a browser context amplifying requests, breaking the invariant that retries bounded and idempotent, and leading to: amplification/dos?

## Target
- File/function: `packages/api-clients/admin-api-client/src/validations.ts` -> `validateServerSideUsage`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a server-side usage guard bypassed from a browser context
- Exploit idea: generateHttpFetch retries a server-side usage guard bypassed from a browser context amplifying requests
- Invariant to test: retries bounded and idempotent
- Expected Immunefi impact: Amplification/DoS (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: retry-count test
