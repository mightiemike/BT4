# Q4949: http/utils — cookie attribute injection

## Question
Can an unprivileged attacker submit two cookies with the same name (duplicate) parsed ambiguously to this module in `http/utils.ts` such that setAndSign writes two cookies with the same name (duplicate) parsed ambiguously enabling attribute injection, breaking the invariant that cookie value encoding prevents attribute break-out, and leading to: session cookie tampering?

## Target
- File/function: `packages/apps/shopify-api/runtime/http/utils.ts` -> (module scope)
- Entrypoint: HTTP request with attacker-controlled cookies/headers/query
- Attacker controls: two cookies with the same name (duplicate) parsed ambiguously
- Exploit idea: setAndSign writes two cookies with the same name (duplicate) parsed ambiguously enabling attribute injection
- Invariant to test: cookie value encoding prevents attribute break-out
- Expected Immunefi impact: Session cookie tampering (In scope: session-cookie forgery, header injection with impact. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: ';'-in-value test
