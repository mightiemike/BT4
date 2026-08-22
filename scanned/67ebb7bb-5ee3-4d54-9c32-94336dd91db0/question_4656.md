# Q4656: admin/authenticate — installed-gate skip

## Question
Can an unprivileged attacker submit a crafted Accept/Sec-Fetch header steering the auth branch to `respondToBouncePageRequest` in `admin/authenticate.ts` such that ensureInstalled/respondToBouncePageRequest serves a crafted Accept/Sec-Fetch header steering the auth branch without an installed session, breaking the invariant that installed check precedes app content, and leading to: access without install/consent?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts` -> `respondToBouncePageRequest`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a crafted Accept/Sec-Fetch header steering the auth branch
- Exploit idea: ensureInstalled/respondToBouncePageRequest serves a crafted Accept/Sec-Fetch header steering the auth branch without an installed session
- Invariant to test: installed check precedes app content
- Expected Immunefi impact: Access without install/consent (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: no-session request test
