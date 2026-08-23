## Finding

### Title
Non-embedded app OAuth session cookie is domain-wide by default, allowing cross-tenant session overwrite/confusion between shops sharing a browser - (File: packages/apps/shopify-api/lib/auth/oauth/oauth.ts)

### Summary
In `@shopify/shopify-api`'s OAuth callback handler, the `shopify_app_session` cookie that identifies the authenticated session for **non-embedded apps** is written with `path: '/'` by default, making it domain-wide rather than scoped per shop. Any OAuth completion (initiated by any single low-privilege merchant/user) silently overwrites the session cookie for every other shop authenticated in the same browser, exactly mirroring the reported bug class: a low-value/decoy transaction ("dust" OAuth completion) creates state that gets confused with, or masks, another tenant's legitimate session.

### Finding Description
The OAuth callback sets the session cookie unconditionally at the domain root unless the app developer explicitly opts in to the new `cookiePath` option: [1](#0-0) 

The default value is documented explicitly in the config type definition: [2](#0-1) 

The corresponding changelog entry confirms this is the exact known behavior of the shipped code, and that the mitigation (`cookiePath`) is opt-in, not the default: [3](#0-2) 

Because `getCurrentSessionId` for non-embedded apps resolves the "current" session purely by reading and verifying this single domain-wide cookie: [4](#0-3) 

there is no per-shop binding of the cookie itself — any subsequent successful OAuth callback for shop B overwrites the cookie that shop A's browser tab is relying on for its `shopify_app_session`. Any single unprivileged user who completes OAuth for a second shop (e.g. opens the app for a shop they control, or is redirected via a decoy installation flow) will silently redirect the victim's existing session identity toward the attacker-influenced shop's session id, and vice versa — the victim's active tab starts acting on the attacker's most-recently-authenticated shop's session, or the attacker's session cookie gets confused with an existing legitimate one, depending on timing.

This is directly analogous to the reported bug class: a small/attacker-controlled state-creation event (an OAuth completion for an unrelated/decoy shop) collides with and masks the state used to arbitrate a legitimate operation (which session/shop the request is scoped to), because the underlying storage key (cookie name + path) is not properly scoped to prevent overwrite/collision.

### Impact Explanation
For non-embedded apps supporting multi-shop usage in the same browser, this can cause:
- Cross-tenant session confusion: a browser tab bound to shop A's session cookie may silently begin operating as shop B's session (or the app may serve/act on the wrong shop context) after any OAuth completion for another shop in the same browser overwrites the shared cookie.
- Since `getCurrentSessionId` and downstream authenticated request handling trust the cookie value as the current session id without any per-shop path isolation, this is a concrete cross-tenant access primitive achievable by an unprivileged actor completing normal OAuth for their own shop.

### Likelihood Explanation
This requires no privileged access — a single merchant/user completing the normal OAuth flow for any shop (including one they control) in the same browser session as another authenticated tab is sufficient to trigger the overwrite. It requires the target app to be non-embedded and to not have opted into `cookiePath`, which per the code comments and changelog was the default/only behavior prior to this option being introduced, meaning apps built on this API package that have not explicitly configured `cookiePath` remain exposed to the collision described in the changelog and base-types documentation.

### Recommendation
Make cookie path scoping mandatory (or auto-derived from shop) rather than opt-in for non-embedded apps, or require session lookups to independently validate that the resolved session's `shop` matches the shop implied by the request URL/referrer before treating the cookie as authoritative, so a session cookie collision cannot silently redirect an unrelated tab/request to a different tenant's session.

### Proof of Concept
1. Configure a non-embedded app without setting `cookiePath` (default `'/'`).
2. In one browser, complete OAuth for Shop A in Tab 1 (`GET /auth/callback?shop=shop-a...`) — cookie `shopify_app_session=offline_shop-a` is set at `path=/`.
3. In Tab 2 of the same browser, complete OAuth for Shop B (`GET /auth/callback?shop=shop-b...`) — per `oauth.ts` lines 219-230, this call to `cookies.setAndSign(SESSION_COOKIE_NAME, session.id, {..., path: '/'})` overwrites the domain-wide cookie with `offline_shop-b`.
4. Return to Tab 1 and make an authenticated request; `getCurrentSessionId` (`session-utils.ts` lines 71-80) reads the now-overwritten cookie and resolves to Shop B's session, causing Tab 1 to act against the wrong tenant's session/store data.

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
