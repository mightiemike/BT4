# Q3010: http/cookies — header canonicalization gap

## Question
Can an unprivileged attacker submit a request with conflicting Host vs X-Forwarded-Host to `setAndSign` in `http/cookies.ts` such that canonicalizeHeaders/setAndSign mis-normalizes a request with conflicting Host vs X-Forwarded-Host, breaking the invariant that header lookup is case/format stable, and leading to: auth header confusion?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/cookies.ts` -> `setAndSign`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a request with conflicting Host vs X-Forwarded-Host
- Exploit idea: canonicalizeHeaders/setAndSign mis-normalizes a request with conflicting Host vs X-Forwarded-Host
- Invariant to test: header lookup is case/format stable
- Expected Immunefi impact: Auth header confusion (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: mixed-case header test
