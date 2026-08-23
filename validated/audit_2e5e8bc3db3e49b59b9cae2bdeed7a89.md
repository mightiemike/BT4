## Analog Vulnerability Found

### Title
Open Redirect via Unrestricted `exitIframe` Destination in `sanitizeRedirectUrl` - (File: `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts`)

### Summary
The reported bug is a "blacklist implemented in the wrong context" bug class: a security check exists in one code path but not in the equivalent code path that shares the same privilege, letting an attacker reach a dangerous sink through the unguarded path. The closest reachable analog in `shopify-app-js` is in the exit-iframe redirect flow used by `shopify-app-remix` (and identically in `shopify-app-react-router`): the function that is supposed to constrain outgoing redirects, `sanitizeRedirectUrl`/`isSafe`, never actually restricts the resulting URL's **hostname** to the app's own domain — it only checks protocol, whitespace, and `///` patterns — so an attacker-supplied absolute URL passes validation untouched.

### Finding Description
`respondToExitIframeRequest` in `authenticate.ts` reads the `exitIframe` query parameter directly from an **unauthenticated** incoming request and forwards it to `renderAppBridge` as the redirect target: [1](#0-0) 

This call happens before any session/shop validation in `authenticateAdmin`: [2](#0-1) 

`renderAppBridge` passes that attacker-controlled value into `sanitizeRedirectUrl(config.appUrl, redirectTo.url)`, whose result is emitted into an inline `<script>window.open(...)</script>`: [3](#0-2) 

The core defect is in `isSafe`/`sanitizeRedirectUrl`: it resolves the supplied `redirectUrl` against `domain` using `new URL(redirectUrl, domain)`, but when `redirectUrl` is itself an absolute URL (e.g. `https://evil.example`), the `domain` base is ignored entirely by the `URL` constructor. The function only rejects file URIs (`///`), whitespace, disallowed protocols, and malformed relative paths — it never verifies that the resulting `url.hostname` matches the app's configured domain: [4](#0-3) 

The existing test suite for this helper confirms the omission — every test case only exercises protocol/whitespace/relative-path checks, and none tests that an absolute cross-origin URL is rejected: [5](#0-4) 

This is precisely analogous to the Rubic finding: a "safety" check (blacklist / allowlist of destination) is implemented, but it is incomplete/misapplied in the specific context where an unauthenticated caller can drive the value all the way to the sink, so the check provides no real protection in that path.

### Impact Explanation
An anonymous attacker can craft a link to the app's `exitIframePath` with `?exitIframe=https://attacker.example/phish&shop=<victim-shop>.myshopify.com&host=<base64host>`. When a merchant clicks it while embedded/loading the app, the returned HTML executes `window.open("https://attacker.example/phish", "_top")`, redirecting the top-level frame away from Shopify admin to an attacker-controlled page. This is a classic open-redirect that can be used for OAuth/session phishing (the redirected page can impersonate the Shopify login/consent screen) or for chaining with other flows (e.g., leaking `host`/`shop` params or session tokens to the attacker domain).

### Likelihood Explanation
The `exitIframePath` endpoint is reachable by any anonymous party (no session/shop validation happens before `respondToExitIframeRequest` runs), and the `exitIframe` query parameter is user-controlled and forwarded unmodified. Exploitation requires only that the merchant/user click a crafted link — no privileged access or secret is needed. The identical pattern exists in `shopify-app-react-router`'s equivalent files.

### Recommendation
In `sanitizeRedirectUrl`/`isSafe`, after resolving `url = new URL(redirectUrl, domain)`, explicitly verify that `url.origin === new URL(domain).origin` (or that `url.hostname` matches the app's own host) before allowing the redirect, rejecting any absolute URL whose origin differs from the app's configured domain. Apply the same fix consistently across `shopify-app-remix` and `shopify-app-react-router` copies of `validate-redirect-url.ts`.

### Proof of Concept
1. Deploy an embedded Shopify app using `shopify-app-remix` with `exitIframePath` configured (default enabled for embedded apps).
2. As an anonymous user, request:
   `GET {appUrl}{exitIframePath}?shop=some-shop.myshopify.com&host=<base64host>&exitIframe=https://attacker.example/phish`
3. `respondToExitIframeRequest` extracts `exitIframe` and calls `renderAppBridge(..., {url: 'https://attacker.example/phish'})` before any auth check.
4. `sanitizeRedirectUrl(config.appUrl, 'https://attacker.example/phish')` returns the URL unchanged since it passes all `isSafe` checks (valid https protocol, no whitespace, no `///`, no `//` in pathname).
5. The response HTML contains `<script>window.open("https://attacker.example/phish", "_top")</script>`, redirecting the browser's top frame to the attacker's site.

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts (L68-80)
```typescript
  async function respondToExitIframeRequest(request: Request) {
    const url = new URL(request.url);

    if (url.pathname === config.auth.exitIframePath) {
      const destination = url.searchParams.get('exitIframe')!;

      logger.debug('Rendering exit iframe page', {
        shop: getShopFromRequest(request),
        destination,
      });
      throw renderAppBridge({config, logger, api}, request, {url: destination});
    }
  }
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts (L143-150)
```typescript
  return async function authenticateAdmin(request: Request) {
    try {
      respondToBotRequest(params, request);
      respondToOptionsRequest(params, request);
      await respondToBouncePageRequest(request);
      await respondToExitIframeRequest(request);
      await strategy.respondToOAuthRequests(request);

```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/render-app-bridge.ts (L14-28)
```typescript
export function renderAppBridge(
  {config}: BasicParams,
  request: Request,
  redirectTo?: RedirectToOptions,
): never {
  let redirectToScript = '';
  if (redirectTo) {
    const destination = sanitizeRedirectUrl(config.appUrl, redirectTo.url);

    const target = redirectTo.target ?? '_top';

    redirectToScript = `<script>window.open(${JSON.stringify(
      destination.toString(),
    )}, ${JSON.stringify(target)})</script>`;
  }
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts (L16-53)
```typescript
function isSafe(
  domain: string,
  redirectUrl: unknown,
  requireSSL: boolean | undefined = true,
): redirectUrl is string {
  if (typeof redirectUrl !== 'string') {
    return false;
  }

  if (
    FILE_URI_MATCH.test(redirectUrl) ||
    WHITESPACE_CHARACTER.test(redirectUrl)
  ) {
    return false;
  }

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
}
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/__tests__/validate-redirect-url.test.ts (L1-84)
```typescript
import {ShopifyError} from '@shopify/shopify-api';

import {APP_URL} from '../../../../__test-helpers';
import {sanitizeRedirectUrl} from '../validate-redirect-url';

describe('sanitizeRedirectUrlFactory', () => {
  it('throws ShopifyError with non-string types', () => {
    // THEN
    expect(() => sanitizeRedirectUrl(APP_URL, 123)).toThrow(ShopifyError);
  });

  it('throws ShopifyError with file URLs', () => {
    // THEN
    expect(() => sanitizeRedirectUrl(APP_URL, '///path/to/a/file')).toThrow(
      ShopifyError,
    );
  });

  it('throws ShopifyError if URL contains whitespaces', () => {
    // THEN
    expect(() =>
      sanitizeRedirectUrl(APP_URL, '/fine/url/but/it has spaces'),
    ).toThrow(ShopifyError);
  });

  it('throws ShopifyError with invalid URLs', () => {
    // THEN
    expect(() => sanitizeRedirectUrl('not a domain', '/valid/path')).toThrow(
      ShopifyError,
    );
  });

  it('throws ShopifyError with invalid relative URLs', () => {
    // THEN
    expect(() => sanitizeRedirectUrl(APP_URL, '/valid//path')).toThrow(
      ShopifyError,
    );
  });

  it('throws ShopifyError with invalid protocol', () => {
    // THEN
    expect(() =>
      sanitizeRedirectUrl(APP_URL, 'javascript:alert("nope")'),
    ).toThrow(ShopifyError);
  });

  it('throws ShopifyError when SSL is required and an HTTP address is given', () => {
    // THEN
    expect(() =>
      sanitizeRedirectUrl(APP_URL, 'http://example.com', {requireSSL: true}),
    ).toThrow(ShopifyError);
  });

  it('returns undefined if not set to throw', () => {
    // THEN
    expect(
      sanitizeRedirectUrl(APP_URL, 'http://example.com', {
        requireSSL: true,
        throwOnInvalid: false,
      }),
    ).toBeUndefined();
  });

  it('succeeds on a valid URL', () => {
    // THEN
    expect(
      sanitizeRedirectUrl(APP_URL, '/my/app/path', {requireSSL: true}),
    ).toEqual(new URL(`${APP_URL}/my/app/path`));
  });

  it('succeeds on a valid URL when not throwing', () => {
    // THEN
    expect(
      sanitizeRedirectUrl(APP_URL, '/my/app/path', {throwOnInvalid: false}),
    ).toEqual(new URL(`${APP_URL}/my/app/path`));
  });

  it('succeeds on a valid HTTP URL when not requiring SSL', () => {
    // THEN
    expect(
      sanitizeRedirectUrl(APP_URL, 'http://my/app/path', {requireSSL: false}),
    ).toEqual(new URL('http://my/app/path'));
  });
});
```
