Confirmed: `respondToBotRequest` runs before `respondToExitIframeRequest`, so bot-blocking is not bypassed for that check, but there is no session-token/auth check before `respondToExitIframeRequest` — it runs unauthenticated, before `getSessionTokenContext`/`validateSessionToken`. The `destination` is taken raw from the query string and only passed through `sanitizeRedirectUrl`, which validates protocol (`http:`/`https:`), absence of `file://`, whitespace, and malformed relative paths — but performs **no domain/origin allow-listing** against `config.appUrl`.

### Title
Open Redirect via unauthenticated `exitIframe` query parameter in exit-iframe route - (File: `packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts` / `packages/apps/shopify-app-react-router/src/server/authenticate/admin/authenticate.ts`)

### Summary
`respondToExitIframeRequest` reads the `exitIframe` query parameter directly from the request URL and passes it unauthenticated to `renderAppBridge`, which only checks the URL's protocol via `sanitizeRedirectUrl` in `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts` — never validating that the destination belongs to `config.appUrl` or an allow-listed domain. This lets an unauthenticated attacker craft a link to the app's exit-iframe route pointing to any external `https://`/`http://` URL, and have the merchant's browser open it via `window.open(destination, "_top")`.

### Finding Description
In `authenticateAdmin` (`packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts:143-149`), the order of execution is `respondToBotRequest` → `respondToOptionsRequest` → `respondToBouncePageRequest` → `respondToExitIframeRequest` → `strategy.respondToOAuthRequests`, all executed before any session-token/HMAC validation (`getSessionTokenContext`/`validateSessionToken` happen later, at line 159-160). `respondToExitIframeRequest` (lines 68-80) does:
```
const destination = url.searchParams.get('exitIframe')!;
throw renderAppBridge({config, logger, api}, request, {url: destination});
```
`renderAppBridge` (`packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/render-app-bridge.ts:14-28`) calls `sanitizeRedirectUrl(config.appUrl, redirectTo.url)`, which only enforces that the URL: is a string, has no `file://` triple-slash, no whitespace, no malformed relative path, and has protocol `http:`/`https:`. It does **not** check that `url.origin === new URL(config.appUrl).origin` or any allow-list — unlike `redirectFactory` (`helpers/redirect.ts:45`) which explicitly checks `isSameOrigin` for propagating query params (but that check is unrelated to whether the redirect target itself is restricted). Because `new URL(redirectUrl, domain)` returns the redirect URL as-is when it's already absolute, an attacker-supplied `https://evil.example.com` passes validation unchanged.

This is confirmed by the very own test suite (`packages/apps/shopify-app-remix/src/server/authenticate/admin/__tests__/exit-i-frame-path.test.ts`), which only asserts rejection of `file:///not/allowed/path`, never testing (or blocking) an arbitrary external `https://` domain — the accepted test case uses `config.appUrl` itself but no code path restricts the value to it.

### Impact Explanation
An attacker can produce a URL such as `https://victim-app.example.com/auth/exit-iframe?exitIframe=https://evil.example.com&shop=victim.myshopify.com` and send/embed it to a merchant. When loaded, the app responds 200 with an HTML page containing `<script>window.open("https://evil.example.com/", "_top")</script>`, causing the merchant's top-level browsing context to navigate to the attacker's page — impersonating an app-originated redirect. This matches Shopify's "open redirect" bounty class with phishing consequence, reachable with no authentication whatsoever (runs before session-token or HMAC verification).

### Likelihood Explanation
Fully reachable pre-authentication by any anonymous actor who can get a merchant to click a link (or auto-load it, e.g., via an iframe/ad) to the app's own domain — no secrets, tokens, or special role required. This is a default-configuration issue (uses `config.auth.exitIframePath`, which every embedded app built on `shopify-app-remix`/`shopify-app-react-router` exposes), so it is broadly and repeatably exploitable.

### Recommendation
In `sanitizeRedirectUrl` (`validate-redirect-url.ts`), additionally require that the resolved `url.origin` equals `new URL(domain).origin` for absolute destination URLs (or otherwise enforce an explicit allow-list of trusted origins), rejecting any cross-origin `exitIframe`/redirect target before it reaches `renderAppBridge`.

### Proof of Concept
```ts
// packages/apps/shopify-app-remix/src/server/authenticate/admin/__tests__/exit-i-frame-path.test.ts
test('BUG: allows redirecting to an attacker-controlled domain', async () => {
  const config = testConfig();
  const shopify = shopifyApp(config);

  const evilUrl = 'https://evil.example.com';
  const exitTo = encodeURIComponent(evilUrl);
  const url = `${APP_URL}/auth/exit-iframe?exitIframe=${exitTo}&shop=${TEST_SHOP}`;

  const response = await getThrownResponse(
    shopify.authenticate.admin,
    new Request(url), // no auth header, no session token, unauthenticated
  );

  const responseText = await response.text();
  expect(response.status).toBe(200);
  // Vulnerable: the app renders a script that navigates the top frame to an
  // attacker-controlled, cross-origin URL with no origin check.
  expect(responseText).toContain(
    `<script>window.open("${evilUrl}/", "_top")</script>`,
  );
});
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
