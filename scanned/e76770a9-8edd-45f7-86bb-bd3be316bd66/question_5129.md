# Q5129: src/validations — server-guard bypass

## Question
Can an unprivileged attacker submit a redirect followed by http-fetch to an attacker location to `validateServerSideUsage` in `src/validations.ts` such that validateServerSideUsage bypassed via a redirect followed by http-fetch to an attacker location, breaking the invariant that server-only APIs unreachable from browser, and leading to: credential exposure?

## Target
- File/function: `packages/api-clients/admin-api-client/src/validations.ts` -> `validateServerSideUsage`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a redirect followed by http-fetch to an attacker location
- Exploit idea: validateServerSideUsage bypassed via a redirect followed by http-fetch to an attacker location
- Invariant to test: server-only APIs unreachable from browser
- Expected Immunefi impact: Credential exposure (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: browser-context test
