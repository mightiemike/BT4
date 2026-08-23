Based on my investigation, this is confirmed as a genuine reachable finding.

### Title
Open redirect via unvalidated `exitIframe` destination domain in `respondToExitIframeRequest` - (File: packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts)

### Summary
The Cowswap `preSign` finding is a "missing basic validation of an attacker-influenced destination/parameter" bug class. The analogous flaw here is in the embedded-admin auth flow's exit-iframe handler: it accepts a fully attacker-controlled `exitIframe` query parameter and only checks that it uses `http:`/`https:` protocol — it never validates that the destination belongs to the app's own origin or to Shopify. This is reachable by any anonymous request, before any session/HMAC/CSRF check occurs.

### Finding Description
`respondToExitIframeRequest` in [1](#0-0)  reads the `exitIframe` query parameter directly from the URL and passes it unmodified to `renderAppBridge` as the redirect destination. This handler runs unconditionally for any request whose path matches `config.auth.exitIframePath`, before OAuth/session validation (it is called early in `authenticateAdmin`, at [2](#0-1) ).

`renderAppBridge` then calls `sanitizeRedirectUrl(config.appUrl, redirectTo.url)` [3](#0-2) . Looking at the sanitizer's implementation, `isSafe` only rejects file URIs, whitespace, invalid relative-URL patterns, and non-`http(s)` protocols — it does **not** check that the resulting URL's host/origin matches `config.appUrl` or any Shopify domain: [4](#0-3) . As a result, `sanitizeRedirectUrl(config.appUrl, "https://attacker.example.com")` is treated as safe and returned as-is.

This is corroborated by the test suite, which explicitly asserts that a fully external `exitTo=config.appUrl` value is accepted and rendered into a `window.open` script, and that only `file://` protocol is rejected — no test asserts rejection of a different external `https://` origin: [5](#0-4) [6](#0-5) . The unit tests for `sanitizeRedirectUrl` itself confirm this: an arbitrary `http://example.com` is only rejected when `requireSSL: true` is explicitly passed, and is otherwise accepted regardless of domain: [7](#0-6) . `renderAppBridge` never passes `requireSSL`, so `https://attacker.example.com` fully bypasses validation.

The rendered response injects the destination directly into a `window.open(...)` call with target `_top` by default, meaning the top-level browsing context (not just an iframe) is navigated to the attacker URL [8](#0-7) .

### Impact Explanation
An attacker can craft a link such as:
```
https://<victim-app-domain>/auth/exit-iframe?exitIframe=https%3A%2F%2Fattacker.example.com%2Fphish&shop=victim-shop.myshopify.com
```
This is served from the legitimate app's own domain (passing any domain-based phishing checks/link previews) and, when opened by a merchant admin — especially inside the Shopify Admin iframe context where this exit-iframe flow is expected — causes the top-level window to navigate to an attacker-controlled site. This is a classic open-redirect/phishing primitive: it can be used to impersonate the Shopify OAuth/login flow on a look-alike domain to harvest merchant credentials or trick merchants into approving malicious OAuth scope grants, since the redirect originates from a trusted app URL.

### Likelihood Explanation
High reachability: the endpoint requires no authentication, no valid session, and no HMAC — it's hit unconditionally for any request matching `config.auth.exitIframePath`, which is a default, predictable, and configurable-but-typically-static route (e.g. `/auth/exit-iframe`). Exploitation only requires a single unauthenticated GET request with attacker-chosen query parameters, and social engineering to get a merchant to click the crafted link.

### Recommendation
In `sanitizeRedirectUrl` (or specifically in `renderAppBridge`/`respondToExitIframeRequest`), validate that the destination URL's origin matches `config.appUrl`'s origin (or an explicit Shopify admin/myshopify allowlist), rejecting any cross-origin destination by default. Only allow cross-origin destinations for specifically vetted paths (e.g., `admin.shopify.com`) as already done elsewhere in the codebase via `sanitizeShop`/`sanitizeHost` domain-allowlist patterns (see `packages/apps/shopify-api/lib/utils/shop-validator.ts`). This mirrors the recommended fix pattern in the original report: validate the untrusted parameter (here, destination origin) rather than accepting any well-formed value.

### Proof of Concept
1. Deploy/target a Shopify embedded app built with `shopify-app-remix` that uses default config (`config.auth.exitIframePath` typically `/auth/exit-iframe`).
2. Send/click:
   ```
   GET https://victim-app.example.com/auth/exit-iframe?exitIframe=https%3A%2F%2Fattacker.example.com%2Ffake-shopify-login&shop=victim-shop.myshopify.com
   ```
3. The server responds 200 with an HTML page (per `exit-i-frame-path.test.ts` behavior) containing:
   ```html
   <script data-api-key="..." src="https://cdn.shopify.com/shopifycloud/app-bridge.js"></script>
   <script>window.open("https://attacker.example.com/fake-shopify-login", "_top")</script>
   ```
4. The browser's top-level context navigates away from Shopify/the app to the attacker's phishing page, as confirmed by the existing test asserting the exact `window.open` script content for an external `exitTo` value: [9](#0-8) .

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts (L143-149)
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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/__tests__/exit-i-frame-path.test.ts (L13-40)
```typescript
describe('authorize.admin exit iframe path', () => {
  test('Uses App Bridge to exit iFrame when the url matches auth.exitIframePath', async () => {
    // GIVEN
    const config = testConfig();
    const shopify = shopifyApp(config);

    // WHEN
    const exitTo = encodeURIComponent(config.appUrl);
    const url = `${APP_URL}/auth/exit-iframe?exitIframe=${exitTo}&shop=${TEST_SHOP}`;
    const response = await getThrownResponse(
      shopify.authenticate.admin,
      new Request(url),
    );

    // THEN
    const responseText = await response.text();
    expect(response.status).toBe(200);
    expect(response.headers.get('content-type')).toBe(
      'text/html;charset=utf-8',
    );
    expect(responseText).toContain(
      `<script data-api-key="${config.apiKey}" src="${APP_BRIDGE_URL}"></script>`,
    );
    expect(responseText).toContain(
      `<script>window.open("${decodeURIComponent(exitTo)}/", "_top")</script>`,
    );
    expectDocumentRequestHeaders(response);
  });
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/__tests__/exit-i-frame-path.test.ts (L88-98)
```typescript
  test('refuses to redirect to invalid URLs', async () => {
    // GIVEN
    const shopify = shopifyApp(testConfig());
    const exitTo = encodeURIComponent('file:///not/allowed/path');
    const url = `${APP_URL}/auth/exit-iframe?exitIframe=${exitTo}&shop=${TEST_SHOP}`;

    // THEN
    await expect(shopify.authenticate.admin(new Request(url))).rejects.toThrow(
      ShopifyError,
    );
  });
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/__tests__/validate-redirect-url.test.ts (L47-61)
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
```
