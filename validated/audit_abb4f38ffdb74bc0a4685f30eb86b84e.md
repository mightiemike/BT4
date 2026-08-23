### Title
Non-embedded session lookup accepts a session whose `shop` does not match the request's `shop` parameter, enabling cross-tenant session reuse - ([File: packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts])

### Summary
The reported Fractional bug is caused by the code accepting an old/unrelated state object (a previously-committed but failed proposal) as valid for a different, currently-successful flow because there is no check binding "this settlement" to "the currently active proposal." The closest reachable analog in `shopify-app-js` is in the non-embedded admin authentication path: the session used to authenticate a request is resolved purely from a signed cookie value (a session ID) and is never checked against the `shop` query parameter of the incoming request, so a stale/foreign session can be accepted as valid for an unrelated shop context.

### Finding Description
For non-embedded apps, `getSessionTokenContext` reads the shop straight from the `shop` URL parameter, and obtains the session id independently, solely from the `shopify_app_session` cookie: [1](#0-0) 

`getCurrentSessionId` (invoked as `api.session.getCurrentId`) for non-embedded apps does nothing more than verify the cookie's HMAC signature and return whatever session id is stored in it — it has no knowledge of, and performs no comparison against, the `shop` query parameter: [2](#0-1) 

That session id is then used to load a `Session` object from storage, and `AuthCodeFlowStrategy.authenticate` uses that session as long as it `isActive()` — it never checks `session.shop === shop`: [3](#0-2) 

The library's own documentation for `cookiePath` explicitly acknowledges the root cause: because the session cookie defaults to `path: '/'` (domain-wide), it is not scoped per shop, so authenticating a second shop in the same browser overwrites the cookie used by the first shop's tab: [4](#0-3) 

This is structurally identical to the Fractional bug class: just as `settleVault` accepted any `committed` proposal (stale or current) because there was no "active proposal id" binding the settlement call to the specific proposal that succeeded, here `authenticate()` accepts any active session referenced by the cookie because there is no binding ("active shop id") tying the session to the specific `shop` the request claims to be for.

### Impact Explanation
In a non-embedded app, if a merchant/user's browser holds a `shopify_app_session` cookie for Shop B (e.g., from previously installing/using the app for a second store, or because the app is used with multiple stores under one browser profile as documented), a request rendered/routed for Shop A (`?shop=shop-a.myshopify.com`) will be authenticated and served using Shop B's session and access token, without any mismatch being detected or rejected. This can result in cross-tenant data exposure or actions being performed against the wrong store's Admin API using the wrong store's access token, entirely from an unprivileged, single-browser workflow — no attacker-controlled network position or secret leak is required.

### Likelihood Explanation
This is likely to be hit unintentionally by any non-embedded app that is used by a single user across more than one shop (a common scenario for agencies/store managers, or during development/testing with multiple test stores) and that does not set a custom `cookiePath`, which is the documented default (`'/'`). No special privileges beyond normal app usage are required to trigger the condition; it only takes ordinary use across two shops in the same browser context.

### Recommendation
- After resolving `session` via `getCurrentSessionId`/`getCurrentId`, explicitly validate that `session.shop` matches the shop resolved from the request (URL `shop` param) before treating the session as valid; if they diverge, treat it as no session found and redirect to OAuth for the correct shop.
- Alternatively/additionally, encode the shop into the session cookie name/value binding (not just as an opaque session id) so the cookie cannot be silently substituted across shops, and default `cookiePath` to a shop-scoped path rather than `/`.
- Add regression tests mirroring the referenced bug's PoC style: authenticate Shop B, then send a request for Shop A carrying Shop B's session cookie, and assert the request is rejected/redirected to OAuth rather than served using Shop B's session.

### Proof of Concept
1. Configure a non-embedded app (`isEmbeddedApp: false`) with default `cookiePath` (`/`).
2. Complete OAuth for `shop-b.myshopify.com` in a browser; the app sets `shopify_app_session` (cookie path `/`) to Shop B's session id, per `oauth.ts`'s callback handler which signs `SESSION_COOKIE_NAME` with `path: cookiePath` (default `/`): [5](#0-4) 
3. In the same browser, navigate to an app route for a different store, `.../app?shop=shop-a.myshopify.com`, without re-authenticating Shop A.
4. Because the cookie is domain-wide, the request carries Shop B's session cookie. `getSessionTokenContext` reads `shop=shop-a.myshopify.com` from the URL but resolves the session purely from the cookie (Shop B's session id), and `AuthCodeFlowStrategy.authenticate` accepts it as long as `session.isActive()`, without ever comparing `session.shop` to `shop-a.myshopify.com`.
5. The request proceeds using Shop B's access token/session while the surrounding request/URL context is for Shop A, resulting in cross-tenant session use.

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts (L220-228)
```typescript
  const url = new URL(request.url);
  const shop = url.searchParams.get('shop')!;

  const sessionId = await api.session.getCurrentId({
    isOnline: config.useOnlineTokens,
    rawRequest: request,
  });

  return {shop, sessionId, payload: undefined, sessionToken};
```

**File:** packages/apps/shopify-api/lib/session/session-utils.ts (L71-80)
```typescript
    } else {
      log.debug('App is not embedded, looking for session id in cookies', {
        isOnline,
      });

      const cookies = new Cookies(request, {} as NormalizedResponse, {
        keys: [config.apiSecretKey],
      });
      return cookies.getAndVerify(SESSION_COOKIE_NAME);
    }
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts (L74-96)
```typescript
  public async authenticate(
    request: Request,
    sessionContext: SessionContext,
  ): Promise<Session | never> {
    const {api, config, logger} = this;

    const {shop, session} = sessionContext;

    if (!session) {
      logger.debug('No session found, redirecting to OAuth', {shop});
      await redirectToAuthPage({config, logger, api}, request, shop);
    } else if (!session.isActive(config.scopes)) {
      logger.debug(
        'Found a session, but it has expired, redirecting to OAuth',
        {shop},
      );
      await redirectToAuthPage({config, logger, api}, request, shop);
    }

    logger.debug('Found a valid session', {shop});

    return session!;
  }
```

**File:** packages/apps/shopify-api/lib/base-types.ts (L139-153)
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
