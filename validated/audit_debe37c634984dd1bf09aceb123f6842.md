### Title
Weak (shop-independent) session-cookie scoping in non-embedded OAuth flow allows cross-tenant session hijack/fixation - (File: `packages/apps/shopify-api/lib/auth/oauth/oauth.ts`)

### Summary
In the non-embedded OAuth `callback` flow, the `shopify_app_session` cookie that stores the session ID is written with `path: config.cookiePath ?? '/'` [1](#0-0) . By default `cookiePath` is `'/'`, meaning the "salt"/scope that ties a stored session ID to a specific shop is a single domain-wide cookie path rather than anything shop-specific. Just like `VaultFactory::createVault`'s address salt omitting curator/timelock/fee parameters, this cookie key omits the shop from its scope, so any second OAuth completion in the same browser/domain silently overwrites the first, regardless of which shop initiated it.

### Finding Description
`begin()` sets a `STATE_COOKIE_NAME` nonce scoped to `callbackPath` [2](#0-1) , and `callback()` verifies that nonce and then writes the final session cookie: `SESSION_COOKIE_NAME` with `path: cookiePath` where `cookiePath` defaults to `'/'` unless the app explicitly opts in to a shop-scoped path or factory function [3](#0-2) .

Because the cookie name and default path are constant regardless of `shop`, the cookie is a single slot per browser/domain that any completed OAuth flow can claim. If two OAuth flows race or are sequentially completed in the same browser context (e.g. two tabs, or an attacker redirecting a victim's browser through their own OAuth flow after/while the victim has an active session), the later `callback()` call's `cookies.setAndSign(SESSION_COOKIE_NAME, session.id, {path: cookiePath})` overwrites the earlier one's cookie value, exactly as Bob's front-run vault deployment silently replaces/blocks Alice's intended deployment because both compute to the "same slot." The changelog and inline doc comment for `cookiePath` explicitly confirm this is the intended, known root cause: "By default the cookie is written with `path: '/'`... each OAuth callback overwrites the previous cookie, causing all tabs to use the most-recently-authenticated shop" [4](#0-3) .

### Impact Explanation
This is directly analogous to the reported vault issue's two outcomes:
- **DoS/confusion**: a legitimate merchant who just finished OAuth for shop A in one tab has their `shopify_app_session` cookie silently replaced by a second OAuth completion for shop B in another tab, so all of shop A's subsequent requests are served under shop B's session (or fail because the session belongs to the wrong shop), effectively breaking shop A's app usage without any error being surfaced to the user.
- **Session hijack/fixation**: because there is no validation binding the cookie to the shop the current page/route is for beyond what `validate-authenticated-session`-style checks perform downstream, an attacker who can get a victim's browser to complete (or attempt) an OAuth callback for an attacker-controlled shop (e.g. via a crafted link that starts `begin()` for the attacker's own shop) can overwrite the victim's session cookie with the attacker's own session ID. Subsequent app actions in that browser will then operate under the attacker's session context, which can be used to observe or manipulate what the victim does through the attacker's session (a session-fixation-style cross-tenant issue), the closest server-side analog to the "hijacking of the curator role" scenario in the report.

### Likelihood Explanation
This is only reachable in the non-embedded app OAuth flow (`config.isEmbeddedApp === false`) and requires either (a) a merchant/agency legitimately managing multiple shops from the same browser, which is a documented, common scenario per the changelog, or (b) an attacker able to induce two OAuth completions in the victim's browser. The library ships this as the default, unfixed behavior unless the app developer opts in to the `cookiePath` config; there is no enforcement or warning at runtime, only documentation.

### Recommendation
Make the session cookie scope shop-aware by default instead of relying on an opt-in `cookiePath` function, e.g., always include the sanitized shop domain in the cookie name or path (analogous to including all relevant parameters in the vault salt) so that two shops can never collide on the same cookie slot without an explicit opt-out. At minimum, emit a runtime warning when `cookiePath` is left at the default `'/'` for non-embedded, multi-tenant-capable apps.

### Proof of Concept
1. Configure a non-embedded app without setting `cookiePath` (default `'/'` per `base-types.ts`) [3](#0-2) .
2. In one browser, call `shopify.auth.begin({shop: 'shopA.myshopify.com', ...})` then complete `shopify.auth.callback(...)`; observe `shopify_app_session` cookie set with `path=/` and value `session.id` for shop A, as shown in the existing test `uses default path "/" for session cookie when cookiePath is not configured` [5](#0-4) .
3. In the same browser (or same cookie jar), repeat steps for `shop: 'shopB.myshopify.com'`.
4. Observe that the `shopify_app_session` cookie for shop A is overwritten with shop B's `session.id`, matching the documented root cause in the changelog [4](#0-3) .

### Citations

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L93-100)
```typescript
    const state = nonce();

    await cookies.setAndSign(STATE_COOKIE_NAME, state, {
      expires: new Date(Date.now() + 60000),
      sameSite: 'lax',
      secure: true,
      path: callbackPath,
    });
```

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L219-230)
```typescript
    if (!config.isEmbeddedApp) {
      const cookiePath =
        typeof config.cookiePath === 'function'
          ? config.cookiePath(session)
          : (config.cookiePath ?? '/');
      await cookies.setAndSign(SESSION_COOKIE_NAME, session.id, {
        expires: session.expires,
        sameSite: 'lax',
        secure: true,
        path: cookiePath,
      });
    }
```

**File:** packages/apps/shopify-api/lib/base-types.ts (L139-165)
```typescript
  /**
   * The path to use for the OAuth session cookie in non-embedded apps.
   *
   * By default the cookie is written with `path: '/'`, making it domain-wide.
   * This means that when a user authenticates multiple shops in separate tabs,
   * each OAuth callback overwrites the previous cookie, causing all tabs to use
   * the most-recently-authenticated shop.
   *
   * Set this to a string or a function returning a string to scope the cookie to
   * a URL path prefix that is unique per shop. The browser will then maintain
   * one cookie per shop and deliver only the matching one per request.
   *
   * **Requirement:** the configured path must match the actual URL structure of
   * your app — e.g. if each shop lives under `/shops/:shop/`, use that prefix.
   * The library cannot derive this automatically.
   *
   * @example
   * // Static path (single-shop apps or apps with no shop-specific routing)
   * cookiePath: '/'
   *
   * @example
   * // Factory function (multi-shop non-embedded apps)
   * cookiePath: (session) => `/shops/${session.shop}/`
   *
   * @defaultValue `'/'`
   */
  cookiePath?: string | ((session: Session) => string);
```

**File:** packages/apps/shopify-api/CHANGELOG.md (L129-150)
```markdown
- c439dab: Add `cookiePath` config option for multi-shop non-embedded apps

  Non-embedded apps that need to support multiple shops simultaneously in
  separate browser tabs were affected by a cookie collision: all shops shared
  a single `shopify_app_session` cookie at `path=/`, so authenticating a new
  shop would silently overwrite the previous shop's session.

  The new optional `cookiePath` config option lets you scope the session
  cookie to a shop-specific URL prefix, so each shop's cookie coexists
  independently in the browser.

  ```ts
  // Static path (default behaviour, unchanged)
  cookiePath: '/';

  // Factory function — recommended for multi-shop apps
  cookiePath: (session) => `/shops/${session.shop}/`;
  ```

  **Requirement:** the configured path must match your app's URL structure.
  Each shop must be served under a distinct URL prefix for the browser to
  deliver the correct cookie per request.
```

**File:** packages/apps/shopify-api/lib/auth/oauth/__tests__/oauth.test.ts (L725-760)
```typescript
  test('uses default path "/" for session cookie when cookiePath is not configured', async () => {
    const shopify = shopifyApi(testConfig({isEmbeddedApp: false}));

    const beginResponse: NormalizedResponse = await shopify.auth.begin({
      shop,
      isOnline: false,
      callbackPath: '/some-callback',
      rawRequest: request,
    });
    setCallbackCookieFromResponse(
      request,
      beginResponse,
      shopify.config.apiSecretKey,
    );

    const testCallbackQuery: QueryMock = {
      shop,
      state: VALID_NONCE,
      timestamp: getCurrentTimeInSec().toString(),
      code: 'some random auth code',
    };
    const expectedHmac = await generateLocalHmac(shopify.config)(
      testCallbackQuery,
    );
    testCallbackQuery.hmac = expectedHmac;
    request.url += `?${new URLSearchParams(testCallbackQuery).toString()}`;

    queueMockResponse(JSON.stringify({access_token: 'token', scope: ''}));

    const callbackResponse = await shopify.auth.callback({rawRequest: request});
    const responseCookies = Cookies.parseCookies(
      callbackResponse.headers['Set-Cookie'],
    );

    expect(responseCookies.shopify_app_session.path).toEqual('/');
  });
```
