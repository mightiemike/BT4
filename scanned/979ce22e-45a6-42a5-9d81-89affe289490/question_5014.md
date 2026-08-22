# Q5014: utils/fetch-request — server-guard bypass

## Question
Can an unprivileged attacker submit a rawBody proxied to graphqlProxy without re-auth to `fetchRequest` in `utils/fetch-request.ts` such that validateServerSideUsage bypassed via a rawBody proxied to graphqlProxy without re-auth, breaking the invariant that server-only APIs unreachable from browser, and leading to: credential exposure?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/fetch-request.ts` -> `fetchRequest`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a rawBody proxied to graphqlProxy without re-auth
- Exploit idea: validateServerSideUsage bypassed via a rawBody proxied to graphqlProxy without re-auth
- Invariant to test: server-only APIs unreachable from browser
- Expected Immunefi impact: Credential exposure (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: browser-context test
