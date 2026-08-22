# Q3294: middlewares/ensure-installed-on-shop — customer/checkout token reuse

## Question
Can an unprivileged attacker submit a bot user-agent that should be short-circuited to `getRequestShop` in `middlewares/ensure-installed-on-shop.ts` such that getRequestShop accepts a bot user-agent that should be short-circuited from a different session/customer, breaking the invariant that public tokens bound to their session, and leading to: cross-user access?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/ensure-installed-on-shop.ts` -> `getRequestShop`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a bot user-agent that should be short-circuited
- Exploit idea: getRequestShop accepts a bot user-agent that should be short-circuited from a different session/customer
- Invariant to test: public tokens bound to their session
- Expected Immunefi impact: Cross-user access (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: token-reuse test
