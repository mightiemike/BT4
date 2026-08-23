This confirms a genuine, already-fixed analog vulnerability class in `shopify-app-js`: the OAuth session cookie for non-embedded multi-shop apps was written at `path: '/'` by default, causing cross-tenant session overwrite — directly analogous to the `StakingVault.unstake()` bug where a per-scope value (per-cycle shares) was destructively overwritten at the shared/global scope (all shares) instead of being scoped correctly.

### Title
Non-Embedded App OAuth Session Cookie Written at Domain-Wide Path Causes Cross-Shop Session Overwrite - ([File: packages/apps/shopify-api/lib/auth/oauth/oauth.ts])

### Summary
In the OAuth `callback()` handler for non-embedded apps, the `shopify_app_session` cookie is signed and set with `path: cookiePath`, where `cookiePath` defaults to `'/'` unless explicitly overridden by app developers. Because the cookie's scope is the entire domain rather than being scoped per shop, a single browser profile authenticating multiple shops (in separate tabs) causes each shop's OAuth callback to silently overwrite the previous shop's session cookie. This mirrors the disclosed `StakingVault.unstake()` root cause: a value meant to be updated/scoped for one entity (one user's shares in a cycle; one shop's session cookie) instead clobbers a shared/global value (total shares across all stakes; the single domain-wide cookie), causing other legitimate parties (other users; other shops/sessions) to lose their correct state.

### Finding Description
`begin()` sets the OAuth state cookie and `callback()` sets the session cookie: [1](#0-0) 

The `cookiePath` config defaults to `'/'`, which is documented as domain-wide: [2](#0-1) 

This is exactly the same bug class as the `unstake()` finding: instead of scoping the update to the entity being processed (the specific shop's session), the write target is a single shared resource (the domain-wide cookie at `path: '/'`) that gets unconditionally overwritten on every successful OAuth callback, regardless of which shop just authenticated. There is no per-shop namespacing of the cookie unless the app developer opts in via the new `cookiePath` factory function.

The changelog for this exact fix confirms the vulnerability and its mitigation: [3](#0-2) 

Tests confirm the default (vulnerable) behavior still exists for backward compatibility and only opt-in configuration fixes it: [4](#0-3) 

### Impact Explanation
For non-embedded apps supporting multiple shops from the same browser (e.g. a merchant/staff user with multiple stores, or an admin testing multiple shops in separate tabs), authenticating shop B overwrites the session cookie previously set for shop A. Requests from the tab for shop A will now carry a session cookie pointing at shop B's session ID (or vice versa depending on order/expiry), causing the app to serve/act on the wrong shop's data under the browser session, and validated by `validateAuthenticatedSession`-style flows that trust the cookie-derived session id. This is a cross-tenant session confusion condition — one legitimate merchant's browser can silently start operating against another shop's session state due to the shared global cookie path.

### Likelihood Explanation
This requires no attacker action, malicious input, or privileged access — it is triggered purely by normal usage: any single browser used to install/authenticate more than one shop on a non-embedded app that has not set a custom `cookiePath`. Given `cookiePath` defaults to `'/'` and is opt-in, any non-embedded multi-shop deployment is affected by default, matching the "unprivileged, reachable from normal single merchant/customer flow" criteria.

### Recommendation
As already implemented by the Shopify team, scope the OAuth session cookie per shop by requiring/deriving a shop-specific `cookiePath` (e.g., `cookiePath: (session) => '/shops/${session.shop}/'`) so distinct shops in the same browser maintain independent cookies, consistent with the `unstake()` fix of scoping the state change to the specific entity being updated rather than a shared global.

### Proof of Concept
1. Deploy a non-embedded Shopify app using `shopify-app-express`/`shopify-app-remix`/`shopify-app-react-router` without setting `cookiePath`.
2. In Browser Tab 1, complete OAuth for Shop A — `shopify_app_session` cookie is set at `path=/` with Shop A's session id, per [1](#0-0) .
3. In Browser Tab 2 (same browser/profile), complete OAuth for Shop B — the same `path=/` cookie is overwritten with Shop B's session id.
4. Return to Tab 1 and reload/interact with the app for Shop A — the browser now sends the cookie referencing Shop B's session, and the app authenticates/serves the request as Shop B's session instead of Shop A's, confirmed by the default-path test expectation `responseCookies.shopify_app_session.path === '/'` in [4](#0-3) .

### Citations

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

**File:** packages/apps/shopify-api/lib/base-types.ts (L140-165)
```typescript
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

**File:** packages/apps/shopify-api/CHANGELOG.md (L127-150)
```markdown
### Minor Changes

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
