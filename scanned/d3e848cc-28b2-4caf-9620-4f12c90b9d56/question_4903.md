# Q4903: utils/hmac-validator — truthy-coercion of result

## Question
Can an unprivileged attacker submit a base64 signature carrying trailing '=' padding or URL-safe chars to `stringifyQueryForAppProxy` in `utils/hmac-validator.ts` such that a non-boolean return from stringifyQueryForAppProxy is treated as success for a base64 signature carrying trailing '=' padding or URL-safe chars, breaking the invariant that validator returns strict boolean and callers gate on it, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/hmac-validator.ts` -> `stringifyQueryForAppProxy`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: a base64 signature carrying trailing '=' padding or URL-safe chars
- Exploit idea: a non-boolean return from stringifyQueryForAppProxy is treated as success for a base64 signature carrying trailing '=' padding or URL-safe chars
- Invariant to test: validator returns strict boolean and callers gate on it
- Expected Immunefi impact: Auth bypass (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert callers reject non-true results
