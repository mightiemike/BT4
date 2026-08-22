# Q3288: admin/authenticate — customer/checkout token reuse

## Question
Can an unprivileged attacker submit a bot user-agent that should be short-circuited to `respondToBouncePageRequest` in `admin/authenticate.ts` such that respondToBouncePageRequest accepts a bot user-agent that should be short-circuited from a different session/customer, breaking the invariant that public tokens bound to their session, and leading to: cross-user access?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts` -> `respondToBouncePageRequest`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a bot user-agent that should be short-circuited
- Exploit idea: respondToBouncePageRequest accepts a bot user-agent that should be short-circuited from a different session/customer
- Invariant to test: public tokens bound to their session
- Expected Immunefi impact: Cross-user access (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: token-reuse test
