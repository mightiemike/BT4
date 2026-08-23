## Finding

### Title
Domain-wide OAuth session cookie causes cross-tenant session hijacking in non-embedded apps by default - (File: `packages/apps/shopify-api/lib/auth/oauth/oauth.ts`, `packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts`)

### Summary
Analogous to the ProfilePicture bug where a value fixed at one point in time (`subprotocolName`) is silently invalidated/overwritten by an unprivileged actor performing an equivalent, unsynchronized action (front-running registration), the non-embedded OAuth flow in `@shopify/shopify-api` writes the session-identifying cookie (`shopify_app_session`) with a domain-wide default path (`'/'`). When a second, entirely legitimate OAuth completion happens in the same browser (e.g. a merchant/agent operating multiple shops, or simply opening a second tab), the second callback **silently overwrites** the first shop's session cookie. There is no synchronization or shop-scoping by default, so the first tab now unknowingly resumes activity as the second shop's session.

### Finding Description
`begin()` sets the state cookie scoped to `callbackPath`, but the OAuth `callback()` sets the actual session-identity cookie without any shop-specific scoping by default: [1](#0-0) 

The `cookiePath` config option exists specifically to mitigate this, and its doc comment explicitly acknowledges the root cause: [2](#0-1) 

By default `cookiePath` is `'/'` (domain-wide), so unless an app developer explicitly opts into shop-scoped cookie paths, every shop's session cookie for that browser is overwritten by whichever shop completed OAuth most recently — exactly like the ProfilePicture subprotocol being overwritten by anyone re-using the same name, except here the "namespace" is the browser's cookie jar and the actor triggering the overwrite needs no special privilege — just to complete their own, legitimate OAuth flow.

The consuming middleware compounds the impact: `validateAuthenticatedSession` in `shopify-app-express` reads the current session via the cookie, then falls back to `session.shop` when no `shop` query parameter is present on the request, meaning the cross-shop mismatch check is bypassed whenever internal app navigation doesn't carry an explicit `shop` param: [3](#0-2) 

Session retrieval itself simply reads and verifies whatever ID is currently in the (now-overwritten) cookie: [4](#0-3) 

### Impact Explanation
In non-embedded app deployments using the default configuration, a user's browser session for Shop A can be silently swapped for Shop B's session as soon as Shop B (any shop, not necessarily under the same tenant) completes OAuth in the same browser context. Any subsequent request in the Shop A tab that omits the `shop` query parameter will be served, and act, using Shop B's authenticated session — an unintended cross-tenant session confusion condition. This can leak app UI state/data intended for one merchant into another merchant's browsing context and vice versa, and could be leveraged deliberately by getting a victim to authorize a second, attacker-controlled shop install in the background (e.g. via an auto-loading iframe/image triggering `/auth?shop=attacker-shop.myshopify.com`) while the victim continues to use the first tab.

### Likelihood Explanation
This requires no elevated privileges — it triggers whenever two OAuth completions for different shops happen in the same browser and the developer has not set a custom `cookiePath`, which is the default state of the library. This is a realistic scenario for agencies/support staff managing multiple stores, or for any attacker able to induce a background OAuth completion for a second shop in the victim's browser.

### Recommendation
Make cookie scoping shop-aware by default rather than opt-in: derive a shop-specific `cookiePath` (or shop-scoped cookie name) automatically during `callback()`, or fail safe by requiring the `shop` query parameter to always be validated against the loaded session (never falling back to `session.shop`) in `validateAuthenticatedSession` before treating a cookie-derived session as valid.

### Proof of Concept
1. Deploy a non-embedded app using default config (`cookiePath` unset, i.e. `'/'`).
2. In one browser, complete OAuth for `shop-a.myshopify.com` — cookie `shopify_app_session` is set at path `/` for the app's origin.
3. In the same browser (e.g., a second tab, or via a background request the victim's browser follows, such as an `<img>`/hidden iframe pointing at `/auth?shop=shop-b.myshopify.com`), complete OAuth for `shop-b.myshopify.com` — this overwrites the same-named, same-path cookie.
4. Return to the first tab and perform any in-app navigation/fetch that does not include `?shop=shop-a.myshopify.com` (common for SPA-style internal navigation). `validateAuthenticatedSession` loads the session from the (now Shop B) cookie, sees no explicit `shop` query param, sets `shop = session.shop` (Shop B), so the mismatch check at line 122 never fires, and the request proceeds authenticated as Shop B inside what the user believes is their Shop A context. [2](#0-1)

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

**File:** packages/apps/shopify-api/lib/base-types.ts (L140-153)
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
```

**File:** packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts (L106-129)
```typescript
  let shop = api.utils.sanitizeShop(req.query.shop as string) || session?.shop;

  if (
    session &&
    !config.useOnlineTokens &&
    config.future?.expiringOfflineAccessTokens
  ) {
    try {
      session = await ensureOfflineTokenIsNotExpired({api, config}, session);
    } catch (error) {
      config.logger.error(`Failed to refresh offline access token: ${error}`, {
        shop: session.shop,
      });
    }
  }

  if (session && shop && session.shop !== shop) {
    config.logger.debug('Found a session for a different shop in the request', {
      currentShop: session.shop,
      requestShop: shop,
    });

    return redirectToAuth({req, res, api, config});
  }
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
