### Title
Open redirect via unauthenticated `exitIframe` query parameter in `respondToExitIframeRequest` - (File: packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts)

### Summary
`respondToExitIframeRequest` reads the `exitIframe` query parameter directly from the request and passes it to `renderAppBridge` before any session-token or shop validation occurs. The URL is only checked by `sanitizeRedirectUrl`, which validates protocol/format but does not restrict the destination to the app's own domain or Shopify domains, allowing an attacker-controlled URL to be embedded in the App Bridge redirect script.

### Finding Description
In `authStrategyFactory`, `authenticateAdmin` calls `respondToExitIframeRequest(request)` before `getSessionTokenContext` runs [1](#0-0) . When the request path matches `config.auth.exitIframePath`, it extracts `destination` directly from `url.searchParams.get('exitIframe')!` with no shop/session-token validation preceding it, and throws `renderAppBridge({config, logger, api}, request, {url: destination})` [2](#0-1) .

`renderAppBridge` passes this destination through `sanitizeRedirectUrl(config.appUrl, redirectTo.url)` and, if it passes, embeds it verbatim in an inline `<script>window.open(destination, target)</script>` in the HTML response [3](#0-2) .

`sanitizeRedirectUrl`/`isSafe` only rejects `file://` triple-slash patterns, whitespace, malformed URLs, invalid relative-path patterns (`//` or `\\`), and non-`http(s)` protocols; it does **not** check that the resulting host matches the app's domain, `myshopify.com`, or any allow-list of Shopify domains [4](#0-3) . A fully-qualified absolute URL such as `https://evil.example` passes `new URL(redirectUrl, domain)` cleanly, has protocol `https:`, and contains no `///`, backslashes, or whitespace — so `isSafe` returns `true` and the destination is used as-is.

This is confirmed by the existing test suite, which only asserts that `file://...` URLs are rejected while relative paths and the app's own URL are accepted [5](#0-4) ; no test exercises an arbitrary external `https://` host, and none would fail today since the allow-list check does not exist.

### Impact Explanation
An attacker can craft a link `https://{app}/exitIframe?exitIframe=https://evil.example` (adjusted to the app's configured `exitIframePath`) that, when visited by a merchant inside/embedded context, causes the app to serve a 200 HTML response containing `window.open("https://evil.example/", "_top")`. Because this executes with `_top` inside the Shopify admin embedding context, it can redirect the merchant's top-level browsing context away from Shopify admin to an attacker-controlled page, enabling phishing (e.g., a fake Shopify login/OAuth consent page) immediately after the merchant believes they are interacting with the legitimate embedded app. This matches Shopify's open-redirect impact class, usable as a phishing/token-theft launchpad.

### Likelihood Explanation
Fully unauthenticated and requires no secret: the check runs before `getSessionTokenContext`, `validateShopAndHostParams`, or any session/shop validation, and needs only a crafted link click from a merchant. This is a realistic and repeatable social-engineering-adjacent (but still HTTP-only, no privileged access) attack — the same as any classic open-redirect exploit via URL parameter.

### Recommendation
Restrict destinations accepted by `renderAppBridge`/`sanitizeRedirectUrl` for the exit-iframe flow to relative paths or absolute URLs whose host is the configured `config.appUrl` host (or otherwise allow-listed Shopify/app domains), rejecting any absolute URL pointing to a different host.

### Proof of Concept
```ts
test('rejects external absolute exitIframe destinations', async () => {
  const shopify = shopifyApp(testConfig());
  const exitTo = encodeURIComponent('https://evil.example');
  const url = `${APP_URL}/auth/exit-iframe?exitIframe=${exitTo}&shop=${TEST_SHOP}`;

  const response = await getThrownResponse(
    shopify.authenticate.admin,
    new Request(url),
  );
  const responseText = await response.text();

  // Current (vulnerable) behavior: passes and embeds evil.example redirect
  expect(responseText).toContain('window.open("https://evil.example/"');
  // Expected secure behavior: should throw ShopifyError or strip destination
});
```
Running this against current code shows the response contains `window.open("https://evil.example/", "_top")`, confirming the open redirect.

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts (L143-160)
```typescript
  return async function authenticateAdmin(request: Request) {
    try {
      respondToBotRequest(params, request);
      respondToOptionsRequest(params, request);
      await respondToBouncePageRequest(request);
      await respondToExitIframeRequest(request);
      await strategy.respondToOAuthRequests(request);

      // If this is a valid request, but it doesn't have a session token header, this is a document request. We need to
      // ensure we're embedded if needed and we have the information needed to load the session.
      if (!getSessionTokenHeader(request)) {
        validateShopAndHostParams(params, request);
        await ensureAppIsEmbeddedIfRequired(params, request);
        await ensureSessionTokenSearchParamIfRequired(params, request);
      }

      const {payload, shop, sessionId, sessionToken} =
        await getSessionTokenContext(params, request);
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
