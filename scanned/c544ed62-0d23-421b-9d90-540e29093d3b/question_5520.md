# Q5520: http/headers — cookie attribute injection

## Question
Can an unprivileged attacker submit a cookie name differing only by trailing space to `removeHeader` in `http/headers.ts` such that setAndSign writes a cookie name differing only by trailing space enabling attribute injection, breaking the invariant that cookie value encoding prevents attribute break-out, and leading to: session cookie tampering?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/headers.ts` -> `removeHeader`
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: a cookie name differing only by trailing space
- Exploit idea: setAndSign writes a cookie name differing only by trailing space enabling attribute injection
- Invariant to test: cookie value encoding prevents attribute break-out
- Expected Immunefi impact: Session cookie tampering (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: ';'-in-value test
