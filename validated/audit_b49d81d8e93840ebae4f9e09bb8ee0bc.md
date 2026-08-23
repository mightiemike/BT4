### Title
Open redirect via `exitIframe` query parameter accepts arbitrary external URLs - ([File: packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts])

### Finding Description
`respondToExitIframeRequest` reads `exitIframe` directly from the attacker-controlled query string and passes it unchecked as `destination` into `renderAppBridge({config, logger, api}, request, {url: destination})`. [1](#0-0) 

`renderAppBridge` does call a sanitizer, `sanitizeRedirectUrl(config.appUrl, redirectTo.url)`, before emitting the value into a `<script>window.open(...)</script>` payload. [2](#0-1) 

However, `sanitizeRedirectUrl`/`isSafe` only rejects `file://` URIs, whitespace, non-`http(s)` protocols, and double-slash relative-path tricks — it does **not** check that the resulting URL's host matches `config.appUrl` or any Shopify-owned domain. Because `new URL(redirectUrl, domain)` uses `domain` only as a fallback base when `redirectUrl` is relative, a fully-qualified attacker URL like `https://evil.example.com` is treated as absolute, ignores the `domain` base entirely, passes the protocol/format checks, and is returned as a "sanitized" URL. [3](#0-2) 

The existing test suite for this exact function confirms this behavior is by design: it only tests that `file://` URIs are rejected, and that arbitrary same-origin/relative URLs (`config.appUrl`, `/my-path`) are accepted — there is no test asserting that a foreign https URL is rejected. [4](#0-3) 

### Impact Explanation
An attacker can craft a link to `https://<merchant-app-domain>/auth/exit-iframe?exitIframe=https://evil.example.com&shop=<shop>.myshopify.com`. When a merchant admin opens this link (e.g., from a phishing email or an embedded-app frame), the app responds with an App Bridge HTML page containing `window.open("https://evil.example.com", "_top")`, breaking the merchant out of the Shopify admin iframe and navigating the top-level browsing context to the attacker's domain. This is a classic open redirect out of the trusted embedded-app/iframe context and can be used for phishing (spoofed Shopify/App login pages) against merchants who have a legitimate relationship with the app installed. This matches Shopify's bounty impact class of "Open Redirect" from an authentication-adjacent handler.

### Likelihood Explanation
This is trivially reachable by any unauthenticated, unprivileged party who can get a merchant to click/load a URL — no secrets, sessions, or app-developer configuration changes are required. The `exitIframePath` route is a standard, always-present route in this library (`config.auth.exitIframePath`), and the query parameter is read with no allowlist. Exploitation only requires constructing a URL and getting the target to visit it (e.g., via email, chat, or an embedded resource), which is standard open-redirect/phishing delivery.

### Recommendation
In `sanitizeRedirectUrl` (`packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts` and the equivalent file in `shopify-app-react-router`), after parsing the URL, additionally verify that `url.host` equals `new URL(domain).host` (or belongs to an explicit allowlist of Shopify-owned domains such as `*.myshopify.com`/`admin.shopify.com` when redirecting back into Shopify) before accepting it as safe. Reject any absolute URL whose origin differs from the app's own origin/Shopify domain, rather than only checking protocol and path shape.

### Proof of Concept
```ts
// packages/apps/shopify-app-remix/src/server/authenticate/admin/__tests__/exit-i-frame-path.test.ts

test('rejects exitIframe pointing to an external attacker-controlled domain', async () => {
  const config = testConfig();
  const shopify = shopifyApp(config);

  const exitTo = encodeURIComponent('https://evil.example.com');
  const url = `${APP_URL}/auth/exit-iframe?exitIframe=${exitTo}&shop=${TEST_SHOP}`;

  const response = await getThrownResponse(
    shopify.authenticate.admin,
    new Request(url),
  );

  const responseText = await response.text();
  // Currently this PASSES the sanitizer and renders the redirect:
  // `<script>window.open("https://evil.example.com/", "_top")</script>`
  // Expected (fix): should throw ShopifyError('Invalid URL. Refusing to redirect')
  // or the destination host should be forced to match config.appUrl's host.
  expect(responseText).not.toContain('evil.example.com');
});
```
Running this test against the current code shows the response body contains `window.open("https://evil.example.com/", "_top")`, confirming the destination is not validated against the app's own origin or any Shopify-owned domain.

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts (L68-79)
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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts (L16-67)
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

export function sanitizeRedirectUrl<OptionsArg extends Options>(
  domain: string,
  redirectUrl: unknown,
  options: OptionsArg = {} as OptionsArg,
): SanitizedRedirectUrl<OptionsArg> {
  if (isSafe(domain, redirectUrl, options.requireSSL)) {
    return new URL(redirectUrl, domain) as SanitizedRedirectUrl<OptionsArg>;
  } else if (options.throwOnInvalid === false) {
    return undefined as SanitizedRedirectUrl<OptionsArg>;
  } else {
    throw new ShopifyError('Invalid URL. Refusing to redirect');
  }
}
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/__tests__/exit-i-frame-path.test.ts (L71-98)
```typescript
  test('Allows relative paths as exitIframe param', async () => {
    // GIVEN
    const shopify = shopifyApp(testConfig());

    // WHEN
    const exitTo = encodeURIComponent('/my-path');
    const url = `${APP_URL}/auth/exit-iframe?exitIframe=${exitTo}&shop=${TEST_SHOP}`;
    const response = await getThrownResponse(
      shopify.authenticate.admin,
      new Request(url),
    );

    // THEN
    expect(response.status).toBe(200);
    expectDocumentRequestHeaders(response);
  });

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
