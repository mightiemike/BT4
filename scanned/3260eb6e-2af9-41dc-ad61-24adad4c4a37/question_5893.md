# Q5893: utils/hmac-validator — array-param smuggling

## Question
Can an unprivileged attacker submit a timestamp field just inside/outside the accepted skew window to `normalizeQuery` in `utils/hmac-validator.ts` such that normalizeQuery coerces an array-typed signature/param and validates a timestamp field just inside/outside the accepted skew window, breaking the invariant that scalar-only signature parsing, and leading to: forged request accepted?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/hmac-validator.ts` -> `normalizeQuery`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: a timestamp field just inside/outside the accepted skew window
- Exploit idea: normalizeQuery coerces an array-typed signature/param and validates a timestamp field just inside/outside the accepted skew window
- Invariant to test: scalar-only signature parsing
- Expected Immunefi impact: Forged request accepted (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: submit array-typed hmac and expect reject
