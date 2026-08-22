# Q0056: src/validations — ssrf via shop/host

## Question
Can an unprivileged attacker submit a session whose shop drives an outbound Admin API URL to `validateRequiredStoreDomain` in `src/validations.ts` such that validateRequiredStoreDomain builds the outbound API URL from a session whose shop drives an outbound Admin API URL, breaking the invariant that API host restricted to the verified shop's domain, and leading to: ssrf with app credentials?

## Target
- File/function: `packages/api-clients/admin-api-client/src/validations.ts` -> `validateRequiredStoreDomain`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a session whose shop drives an outbound Admin API URL
- Exploit idea: validateRequiredStoreDomain builds the outbound API URL from a session whose shop drives an outbound Admin API URL
- Invariant to test: API host restricted to the verified shop's domain
- Expected Immunefi impact: SSRF with app credentials (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: host-injection URL test
