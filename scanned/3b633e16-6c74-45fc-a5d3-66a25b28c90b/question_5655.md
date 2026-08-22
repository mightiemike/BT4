# Q5655: utils/hmac-validator — array-param smuggling

## Question
Can an unprivileged attacker submit a base64 signature carrying trailing '=' padding or URL-safe chars to `generateLocalHmac` in `utils/hmac-validator.ts` such that generateLocalHmac coerces an array-typed signature/param and validates a base64 signature carrying trailing '=' padding or URL-safe chars, breaking the invariant that scalar-only signature parsing, and leading to: forged request accepted?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/hmac-validator.ts` -> `generateLocalHmac`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: a base64 signature carrying trailing '=' padding or URL-safe chars
- Exploit idea: generateLocalHmac coerces an array-typed signature/param and validates a base64 signature carrying trailing '=' padding or URL-safe chars
- Invariant to test: scalar-only signature parsing
- Expected Immunefi impact: Forged request accepted (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: submit array-typed hmac and expect reject
