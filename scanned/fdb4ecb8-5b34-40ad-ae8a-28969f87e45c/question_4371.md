# Q4371: admin/authenticate — installed-gate skip

## Question
Can an unprivileged attacker submit a request that skips the embedded/installed gate to `addEmbeddedFeatures` in `admin/authenticate.ts` such that ensureInstalled/addEmbeddedFeatures serves a request that skips the embedded/installed gate without an installed session, breaking the invariant that installed check precedes app content, and leading to: access without install/consent?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts` -> `addEmbeddedFeatures`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a request that skips the embedded/installed gate
- Exploit idea: ensureInstalled/addEmbeddedFeatures serves a request that skips the embedded/installed gate without an installed session
- Invariant to test: installed check precedes app content
- Expected Immunefi impact: Access without install/consent (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: no-session request test
