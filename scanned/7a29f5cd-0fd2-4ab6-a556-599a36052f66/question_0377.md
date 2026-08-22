# Q0377: auth/get-embedded-app-url — open redirect via shop

## Question
Can an unprivileged attacker submit a host that passes the '.myshopify.com$' suffix test via subdomain to `getEmbeddedAppUrl` in `auth/get-embedded-app-url.ts` such that getEmbeddedAppUrl treats a host that passes the '.myshopify.com$' suffix test via subdomain as a valid shop and redirects there, breaking the invariant that only allow-listed *.myshopify.com destinations, and leading to: open redirect -> session-token phishing?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/get-embedded-app-url.ts` -> `getEmbeddedAppUrl`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host that passes the '.myshopify.com$' suffix test via subdomain
- Exploit idea: getEmbeddedAppUrl treats a host that passes the '.myshopify.com$' suffix test via subdomain as a valid shop and redirects there
- Invariant to test: only allow-listed *.myshopify.com destinations
- Expected Immunefi impact: Open redirect -> session-token phishing (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: regex bypass test with a host that passes the '.myshopify.com$' suffix test via subdomain
