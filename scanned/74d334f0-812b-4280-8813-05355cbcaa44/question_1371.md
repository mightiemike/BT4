# Q1371: oauth/safe-compare — length-leak in compare

## Question
Can an unprivileged attacker submit a signature over a body with CR/LF or trailing whitespace variants to `safeCompare` in `oauth/safe-compare.ts` such that safeCompare returns early when signature lengths differ, leaking validity, breaking the invariant that timing/length independence of HMAC check, and leading to: auth bypass via signature oracle?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/safe-compare.ts` -> `safeCompare`
- Entrypoint: HMAC-signed request (webhook/app-proxy/OAuth callback) to the app
- Attacker controls: a signature over a body with CR/LF or trailing whitespace variants
- Exploit idea: safeCompare returns early when signature lengths differ, leaking validity
- Invariant to test: timing/length independence of HMAC check
- Expected Immunefi impact: Auth bypass via signature oracle (In scope: signature-verification bypass (forged Shopify request accepted). Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: unit test asserting equal-time handling of mismatched lengths
