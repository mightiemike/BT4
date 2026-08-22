# Q5758: utils/get-hmac-key — array-param smuggling

## Question
Can an unprivileged attacker submit a signature computed over a differently-encoded query string to `getHMACKey` in `utils/get-hmac-key.ts` such that getHMACKey coerces an array-typed signature/param and validates a signature computed over a differently-encoded query string, breaking the invariant that scalar-only signature parsing, and leading to: forged request accepted?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/get-hmac-key.ts` -> `getHMACKey`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: a signature computed over a differently-encoded query string
- Exploit idea: getHMACKey coerces an array-typed signature/param and validates a signature computed over a differently-encoded query string
- Invariant to test: scalar-only signature parsing
- Expected Immunefi impact: Forged request accepted (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: submit array-typed hmac and expect reject
