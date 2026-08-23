### Title
Open redirect via unauthenticated `exitIframe` parameter due to missing host/domain check in `sanitizeRedirectUrl` - (File: packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts)

### Summary
`isSafe()` in `sanitizeRedirectUrl` never verifies that the resolved `URL.host` matches the app's own domain — it only rejects `///`, whitespace, doubled slash/backslash in the *pathname*, and non-http(s) protocols. Any absolute `https://` (or `http://` when SSL isn't required) URL to an arbitrary attacker-controlled host passes validation and is rendered into a `window.open(...)` redirect script. This is reachable pre-authentication through the `exitIframe` query parameter handled by `respondToExitIframeRequest`.

### Finding Description
`authenticateAdmin` in `packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts` calls `respondToExitIframeRequest(request)` before any shop/host validation: [1](#0-0) 

This reads the `exitIframe` query param directly from the unauthenticated request URL and passes it straight into `renderAppBridge`: [2](#0-1) 

`renderAppBridge` calls `sanitizeRedirectUrl(config.appUrl, redirectTo.url)`, whose `isSafe()` check is: [3](#0-2) 

Note that `isSafe()` never compares `url.host` (or `url.hostname`) to `domain`'s host. It only rejects `///` in the raw string, whitespace, `[/\\][/\\]` in `url.pathname`, and non-`http(s)` protocol. This is confirmed by the library's own test suite, which explicitly asserts that an absolute URL with a *different host* than the base domain is accepted: [4](#0-3) 

Regarding the specific backslash-normalization claim in the question: Node's global `URL` implements the WHATWG URL Standard, which treats backslashes as path/authority separators identically to spec-compliant browsers for special schemes (http/https). So there is no Node-vs-browser parsing divergence — both interpret `/\attacker.com/evil` relative to an `https:` base as a network-path reference, producing `host = attacker.com`. The premise that Node treats the backslash as a literal path character while browsers alone treat it as protocol-relative is incorrect; Node and browsers agree. However, this consistent parsing is exactly why the payload works: `isSafe()` accepts it because it never checks `url.host`, and a much simpler payload (`https://attacker.com/evil`, no backslash needed) already passes for the same root-cause reason, as shown by the existing test above.

### Impact Explanation
An unauthenticated attacker can craft a link such as `{appUrl}/auth/exit-iframe?exitIframe=https://attacker.com/phish&shop=any-shop.myshopify.com` (or embed it in an iframe from an untrusted context) and get the app to render `<script>window.open("https://attacker.com/phish","_top")</script>` inside the merchant's browser session, which was rendered by the legitimate app inside the Shopify Admin iframe context. This is an open-redirect out of an authenticated admin surface, usable for phishing (e.g., a fake re-auth/login page) targeting merchants, matching Shopify's "Open Redirect" bounty impact class.

### Likelihood Explanation
Fully unauthenticated and trivially reproducible: the `exitIframe` parameter is read and processed before `validateShopAndHostParams`/session checks, so no valid session, HMAC, or app-developer privilege is required — only a crafted URL that a merchant or admin clicks.

### Recommendation
Add an explicit host allow-list check in `isSafe()`/`sanitizeRedirectUrl`: after parsing the URL, compare `url.host` against the app's own domain (or an explicit allow-list of trusted Shopify hosts, e.g. `*.myshopify.com`, `admin.shopify.com`, `config.appUrl`'s host) for any call path that renders attacker-influenced input (like `exitIframe`), rejecting mismatches. Keep the more permissive host-agnostic behavior only for internal/trusted call sites (e.g. billing confirmation URLs returned by Shopify's API) where the value is not attacker-controlled.

### Proof of Concept
```ts
// packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/__tests__/validate-redirect-url.test.ts
it('allows an absolute URL to an arbitrary external host (open redirect)', () => {
  const result = sanitizeRedirectUrl('https://my-shop.myshopify.com', 'https://attacker.com/evil');
  expect(result.host).toBe('attacker.com'); // passes today, should be rejected
});
```
Request-level PoC:
```
GET /auth/exit-iframe?exitIframe=https%3A%2F%2Fattacker.com%2Fphish&shop=my-shop.myshopify.com HTTP/1.1
Host: my-app.example.com
```
Expected (vulnerable) response body includes:
```html
<script>window.open("https://attacker.com/phish", "_top")</script>
```

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/__tests__/validate-redirect-url.test.ts (L78-83)
```typescript
  it('succeeds on a valid HTTP URL when not requiring SSL', () => {
    // THEN
    expect(
      sanitizeRedirectUrl(APP_URL, 'http://my/app/path', {requireSSL: false}),
    ).toEqual(new URL('http://my/app/path'));
  });
```
