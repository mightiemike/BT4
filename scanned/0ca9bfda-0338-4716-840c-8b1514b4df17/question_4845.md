# Q4845: graphql-client/http-fetch — server-guard bypass

## Question
Can an unprivileged attacker submit a session whose shop drives an outbound Admin API URL to `generateHttpFetch` in `graphql-client/http-fetch.ts` such that validateServerSideUsage bypassed via a session whose shop drives an outbound Admin API URL, breaking the invariant that server-only APIs unreachable from browser, and leading to: credential exposure?

## Target
- File/function: `packages/api-clients/graphql-client/src/graphql-client/http-fetch.ts` -> `generateHttpFetch`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a session whose shop drives an outbound Admin API URL
- Exploit idea: validateServerSideUsage bypassed via a session whose shop drives an outbound Admin API URL
- Invariant to test: server-only APIs unreachable from browser
- Expected Immunefi impact: Credential exposure (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: browser-context test
