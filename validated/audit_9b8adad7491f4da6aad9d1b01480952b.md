### Title
Open Redirect via unvalidated `exitIframe` query parameter in `respondToExitIframeRequest` - ([File: packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts])

### Summary
`respondToExitIframeRequest` reads the `exitIframe` query parameter directly from the incoming request and passes it unmodified into `renderAppBridge`, which only checks that the URL uses `http`/`https` protocol and lacks certain unsafe character patterns — it never verifies the destination's hostname matches `config.appUrl` or any shop-specific allowlist. An unauthenticated attacker can therefore craft a link to the app's `/auth/exit-iframe` (or equivalent) endpoint pointing to an attacker-controlled domain, and the app will render a page that calls `window.open(<attacker-url>, "_top")`, breaking the merchant out of the Shopify Admin iframe into a phishing page.

### Finding Description
In `authStrategyFactory`, `respondToExitIframeRequest` runs before `getSessionTokenContext`/session validation: [1](#0-0) 

It extracts `destination` directly from `url.searchParams.get('exitIframe')` with no sanitation of its own, and forwards it into `renderAppBridge` as the redirect target. `renderAppBridge` calls `sanitizeRedirectUrl(config.appUrl, redirectTo.url)`: [2](#0-1) 

`sanitizeRedirectUrl`/`isSafe` only rejects `file://`-style URIs, whitespace, disallowed protocols, and malformed relative paths (`//` in pathname) — it never compares `url.hostname`/`url.origin` against the `domain` argument (`config.appUrl`): [3](#0-2) 

This is confirmed by the library's own unit test, which asserts a fully foreign absolute URL (`http://example.com`) is accepted as "safe" (failing only due to the SSL requirement, not the domain mismatch): [4](#0-3) 

Because `new URL(absoluteUrl, base)` returns `absoluteUrl` verbatim whenever `absoluteUrl` is itself absolute (ignoring `base`), supplying `exitIframe=https://evil.example.com` passes every check in `isSafe` (valid `https:` protocol, no `///`, no whitespace, no double-slash in pathname) and is accepted as-is. The route match itself (`url.pathname === config.auth.exitIframePath` or `.endsWith(...)` in the react-router variant) requires no authentication, session token, or CSRF-bound state — it fires purely off the URL path, before `respondToBotRequest`'s minimal bot-string check and well before any session-token validation. The identical pattern exists in the `shopify-app-react-router` package: [5](#0-4) 

### Impact Explanation
An unauthenticated attacker can send a merchant (or trick them into clicking) a link to a legitimate app-hosted URL (`https://<app-domain>/auth/exit-iframe?shop=victim.myshopify.com&exitIframe=https://evil.example.com`) that causes the merchant's top-level browser window to navigate to an attacker-controlled site via `window.open(..., "_top")`. Because this originates from the legitimate app domain and is served through the app's own App Bridge exit mechanism, it is a credible phishing vector (open redirect/URL redirection to untrusted site) that could be used to harvest merchant credentials or deliver further attacks, matching Shopify's "Open Redirect"/phishing-enabling impact class.

### Likelihood Explanation
Exploitation requires no privileged access, no secrets, and no prior authentication — only convincing a merchant to click or load a crafted URL (or embedding it in a compromised third-party page). The check bypassed (`sanitizeRedirectUrl`) is on the default code path with default configuration, so it affects any app built with `shopify-app-remix`/`shopify-app-react-router` using default `auth.exitIframePath` settings. This is fully repeatable via a single GET request.

### Recommendation
In `sanitizeRedirectUrl`/`isSafe` (both `shopify-app-remix` and `shopify-app-react-router` copies), reject destinations whose resolved `url.origin` does not equal the `domain` argument's origin, unless the input was a purely relative path. Concretely, after constructing `url = new URL(redirectUrl, domain)`, additionally verify that `redirectUrl` did not itself contain a scheme/host (e.g., check `new URL(domain).origin === url.origin` or restrict input to strings starting with `/` and not `//`) before accepting it as safe for `renderAppBridge`.

### Proof of Concept
```ts
// Jest test demonstrating the unvalidated open redirect
import {sanitizeRedirectUrl} from '../validate-redirect-url';

test('accepts attacker-controlled foreign https destination', () => {
  const result = sanitizeRedirectUrl('https://my-app.example.com', 'https://evil.example.com');
  expect(result.origin).toBe('https://evil.example.com'); // should have thrown/rejected, but doesn't
});
```
Request-level PoC against `shopify-app-remix`:
```
GET /auth/exit-iframe?shop=victim.myshopify.com&exitIframe=https://evil.example.com
```
Expected (vulnerable) response: HTTP 200 HTML containing
`<script>window.open("https://evil.example.com/", "_top")</script>`
with no prior session-token or HMAC validation performed.

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/__tests__/validate-redirect-url.test.ts (L47-62)
```typescript
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
```

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/admin/authenticate.ts (L69-80)
```typescript
  async function respondToExitIframeRequest(request: Request) {
    const url = new URL(request.url);

    if (url.pathname.endsWith(config.auth.exitIframePath)) {
      const destination = url.searchParams.get('exitIframe')!;

      logger.debug('Rendering exit iframe page', {
        shop: getShopFromRequest(request),
        destination,
      });
      throw renderAppBridge({config, logger, api}, request, {url: destination});
    }
```
