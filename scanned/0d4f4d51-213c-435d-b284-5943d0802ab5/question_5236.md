# Q5236: utils/processed-query — cookie attribute injection

## Question
Can an unprivileged attacker submit an oversized cookie/header triggering heavy parsing to this module in `utils/processed-query.ts` such that setAndSign writes an oversized cookie/header triggering heavy parsing enabling attribute injection, breaking the invariant that cookie value encoding prevents attribute break-out, and leading to: session cookie tampering?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/processed-query.ts` -> (module scope)
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: an oversized cookie/header triggering heavy parsing
- Exploit idea: setAndSign writes an oversized cookie/header triggering heavy parsing enabling attribute injection
- Invariant to test: cookie value encoding prevents attribute break-out
- Expected Immunefi impact: Session cookie tampering (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: ';'-in-value test
