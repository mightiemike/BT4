### Title
`sanitizeShop` invariant broken via `sanitizeRedirectUrl`/`isSafe` accepting arbitrary-host redirects - ([File: packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts])

### Summary
`sanitizeShop` itself only validates the `shop`/`host` domain suffix and never touches redirect URLs, so the actual redirect-safety enforcement lives in `sanitizeRedirectUrl`/`isSafe` in `validate-redirect-url.ts` (duplicated in both `shopify-app-remix` and `shopify-app-react-router`). That function checks the URL's *protocol* (must be `https:`, or `http:` when `requireSSL:false`) and rejects file-URIs, whitespace, and double-slash paths, but it **never checks that the resulting URL's host matches the app's own domain**. An attacker-supplied absolute URL (e.g. `https://evil.com/phish`) passes `isSafe` unchanged and is used to build a `window.open(...)` redirect in `renderAppBridge`.

### Finding Description
`isSafe()` in `validate-redirect-url.ts` (both remix and react-router packages) does:
```
url = new URL(redirectUrl, domain);
...
if (!VALID_PROTOCOLS.includes(url.protocol)) return false;
if (requireSSL && url.protocol !== 'https:') return false;
return true;
``` [1](#0-0) 

There is no comparison of `url.hostname`/`url.origin` against the `domain` argument (the app's own URL) anywhere in this function. The existing test suite even documents this: `sanitizeRedirectUrl(APP_URL, 'http://my/app/path', {requireSSL: false})` is expected to **succeed** and return `new URL('http://my/app/path')`, i.e., a URL whose host (`my`) has nothing to do with `APP_URL`. [2](#0-1) 

This function is used by `renderAppBridge` to build a client-side `window.open()` redirect from an attacker/merchant-influenced `redirectTo.url`:
```
const destination = sanitizeRedirectUrl(config.appUrl, redirectTo.url);
redirectToScript = `<script>window.open(${JSON.stringify(destination.toString())}, ...)</script>`;
``` [3](#0-2) 

`sanitizeShop` (`packages/apps/shopify-api/lib/utils/shop-validator.ts`) is a separate function that only validates the `shop` query parameter against a myshopify-domain regex; it has no direct relationship to `sanitizeRedirectUrl` and does not perform any redirect validation itself. [4](#0-3) 

Regarding the "protocol other than https" framing specifically: `isSafe` does correctly reject non-`http`/`https` schemes (e.g., `javascript:`) via `VALID_PROTOCOLS`, and correctly rejects `http:` when `requireSSL` is true (the default). Protocol-relative URLs (`//evil.com/...`) resolve to the base's protocol (`https:`) via `new URL()`, so they don't bypass the protocol check either — the real gap is the missing **host/origin** check, not the protocol check. In other words, the invariant "only same-origin https redirects" is broken not because a non-https protocol is accepted, but because *any host* is accepted as long as the scheme is https (or http when SSL isn't required).

### Impact Explanation
This is an open redirect: an app that passes a merchant/attacker-influenced value into `redirectTo.url` for `renderAppBridge` (or any other consumer of `sanitizeRedirectUrl`) can be made to redirect the top-level browser context to an arbitrary external HTTPS site, since the validator never enforces same-origin-with-`config.appUrl`. Depending on how the host app wires user input into this path, this could be leveraged for phishing following an OAuth-embedded context (token/session theft via a convincing spoofed page), matching the "open redirect leading to token theft" impact class referenced in the question.

### Likelihood Explanation
Exploitability depends entirely on whether a given host application feeds attacker-controlled data into the `redirectTo`/`sanitizeRedirectUrl` call sites (`renderAppBridge`, `redirect-with-exitiframe`, etc.) without an additional application-level allow-list. If a host app relays a `return_to`/`redirect` query parameter as `redirectTo.url`, exploitation is straightforward and repeatable with a single crafted request. I could not fully trace whether any shipped code path in this repo passes truly unprivileged, attacker-controlled input into this argument versus only fixed/internal URLs — that would require deeper tracing of all callers of `renderAppBridge`/`sanitizeRedirectUrl` across `shopify-app-remix` and `shopify-app-react-router`, which I was not able to complete within available searches.

### Recommendation
Add an explicit origin check in `isSafe()`: after constructing `url`, parse `domain` into a `URL` as well and require `url.host === new URL(domain).host` for any redirect that isn't a pure relative path, or explicitly re-validate that the resolved origin equals the app's own origin before returning `true`.

### Proof of Concept
```ts
// packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/__tests__/validate-redirect-url.test.ts
import {sanitizeRedirectUrl} from '../validate-redirect-url';

it('BUG: accepts a cross-origin https URL as "safe"', () => {
  const APP_URL = 'https://my-app.example.com';
  // Attacker-controlled absolute URL to a completely different host
  const result = sanitizeRedirectUrl(APP_URL, 'https://evil.example.net/phish');
  // Currently succeeds and returns the evil URL, demonstrating no host/origin check:
  expect(result.toString()).toBe('https://evil.example.net/phish');
});
```
This currently passes because `isSafe` only checks scheme, never host, confirmed by the existing shipped test at [2](#0-1)  which asserts an analogous cross-host success case.

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts (L32-52)
```typescript
  let url: URL;

  try {
    url = new URL(redirectUrl, domain);
  } catch (_error) {
    return false;
  }

  if (INVALID_RELATIVE_URL.test(url.pathname)) {
    return false;
  }

  if (!VALID_PROTOCOLS.includes(url.protocol)) {
    return false;
  }

  if (requireSSL && url.protocol !== 'https:') {
    return false;
  }

  return true;
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/__tests__/validate-redirect-url.test.ts (L78-83)
```typescript
  it('succeeds on a valid HTTP URL when not requiring SSL', () => {
    // THEN
    expect(
      sanitizeRedirectUrl(APP_URL, 'http://my/app/path', {requireSSL: false}),
    ).toEqual(new URL('http://my/app/path'));
  });
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/render-app-bridge.ts (L21-27)
```typescript
    const destination = sanitizeRedirectUrl(config.appUrl, redirectTo.url);

    const target = redirectTo.target ?? '_top';

    redirectToScript = `<script>window.open(${JSON.stringify(
      destination.toString(),
    )}, ${JSON.stringify(target)})</script>`;
```

**File:** packages/apps/shopify-api/lib/utils/shop-validator.ts (L11-49)
```typescript
export function sanitizeShop(config: ConfigInterface) {
  return (shop: string, throwOnInvalid = false): string | null => {
    let shopUrl = shop;
    const domainsRegex = [
      'myshopify\\.com',
      'shopify\\.com',
      'myshopify\\.io',
      'shop\\.dev',
    ];

    // Add domains from transformations (both source and target)
    if (config.domainTransformations) {
      domainsRegex.push(...getTransformationDomains(config));
    }

    const shopUrlRegex = new RegExp(
      `^[a-zA-Z0-9][a-zA-Z0-9-_]*\\.(${domainsRegex.join('|')})[/]*$`,
    );

    const shopAdminRegex = new RegExp(
      `^admin\\.(${domainsRegex.join('|')})/store/([a-zA-Z0-9][a-zA-Z0-9-_]*)$`,
    );

    const isShopAdminUrl = shopAdminRegex.test(shopUrl);
    if (isShopAdminUrl) {
      shopUrl = shopAdminUrlToLegacyUrl(shopUrl) || '';
    }

    const sanitizedShop = shopUrlRegex.test(shopUrl) ? shopUrl : null;
    if (!sanitizedShop && throwOnInvalid) {
      throw new InvalidShopError('Received invalid shop argument');
    }

    if (sanitizedShop && config.domainTransformations) {
      return applyDomainTransformations(sanitizedShop, config);
    }

    return sanitizedShop;
  };
```
