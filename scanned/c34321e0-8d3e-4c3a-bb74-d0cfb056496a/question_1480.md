# Q1480: utils/fetch-request — token disclosure

## Question
Can an unprivileged attacker submit a response deserialized from an attacker-influenced upstream to `fetchRequest` in `utils/fetch-request.ts` such that fetchRequest places the access token where a response deserialized from an attacker-influenced upstream can read it, breaking the invariant that secrets never reach responses/logs/errors, and leading to: access-token leak?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/fetch-request.ts` -> `fetchRequest`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a response deserialized from an attacker-influenced upstream
- Exploit idea: fetchRequest places the access token where a response deserialized from an attacker-influenced upstream can read it
- Invariant to test: secrets never reach responses/logs/errors
- Expected Immunefi impact: Access-token leak (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert token absent from error/log
