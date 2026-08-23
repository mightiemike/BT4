Confirmed: for non-embedded apps, `getCurrentSessionId` in `packages/apps/shopify-api/lib/session/session-utils.ts` reads the current session **only** from `SESSION_COOKIE_NAME` in the cookie jar, with no cross-check against the `shop` on the request. This means whichever session ID was most recently written to that cookie is unconditionally treated as "the current session" for any subsequent request, regardless of which shop the request is actually for.

### Title
Non-embedded OAuth session cookie is scoped to `path: '/'` by default, causing cross-tenant session overwrite/hijack for multi-shop browsers - (File: packages/apps/shopify-api/lib/auth/oauth/oauth.ts)

### Summary
In the OAuth callback for non-embedded apps, the `shopify_app_session` cookie that stores the authenticated session ID is written with a domain-wide `path` (default `'/'`) unless the app developer explicitly opts into a shop-specific `cookiePath`. Because the cookie key doesn't vary by shop, authenticating a second shop in the same browser silently overwrites the first shop's session cookie value, and `getCurrentSessionId` blindly trusts whatever session ID is present in that single cookie without verifying it matches the shop being requested.

### Finding Description
In `callback()` in `packages/apps/shopify-api/lib/auth/oauth/oauth.ts`, after exchanging the OAuth code for an access token, the session cookie is set like this: [1](#0-0) 

The `cookiePath` defaults to `'/'` when not configured: [2](#0-1) 

Because the cookie name (`SESSION_COOKIE_NAME`) is fixed and its path defaults to `/`, one browser can only ever hold a single value for this cookie across all shops served by the same app domain. When the same browser completes OAuth for a second shop, the new `Set-Cookie` for `shopify_app_session` at `path=/` overwrites the previous shop's cookie value — exactly analogous to the CDP.sol bug where a value that should be additive/tenant-scoped state is instead unconditionally overwritten on every update.

The overwrite becomes a security-relevant issue because `getCurrentSessionId()` in `packages/apps/shopify-api/lib/session/session-utils.ts` reads this cookie and returns its value as *the* current session id for any request, without validating it against the shop implied by the request: [3](#0-2) 

The maintainers themselves acknowledge this exact overwrite bug in the changelog, describing it as a "cookie collision" where "authenticating a new shop would silently overwrite the previous shop's session," and shipped an *opt-in* `cookiePath` mitigation rather than fixing the default: [4](#0-3) 

### Impact Explanation
For any non-embedded app that serves more than one shop from the same top-level domain (a normal Shopify use case, e.g. a merchant with two stores using the same non-embedded app in different browser tabs, or two independent merchants using a shared browser/profile such as a kiosk), the second OAuth completion overwrites the first shop's session cookie. Subsequent unauthenticated-looking requests from the first shop's tab will resolve to the second shop's session id via `getCurrentSessionId`, potentially causing the app to act on/display data using the wrong tenant's session, and vice versa when the first shop's tab is later revisited and the app loads the wrong session. This is a cross-tenant session confusion, not merely a UX inconvenience, since downstream code trusts the cookie-derived session id as authoritative without correlating it back to the shop.

### Likelihood Explanation
This requires no attacker action beyond ordinary usage: a single browser authenticating two shops against the same non-embedded app deployment (default configuration, since `cookiePath` is opt-in and most integrators are unaware of it) is sufficient to trigger the overwrite. No secrets or privileged access are needed.

### Recommendation
Make the session cookie path (or name) shop-scoped by default, e.g. by deriving the cookie name/path from the shop, or by having `getCurrentSessionId` cross-validate the loaded session's `shop` field against the shop derived from the request (query param, referrer, or App Bridge host) before treating it as current, and rejecting mismatches instead of silently trusting the cookie contents.

### Proof of Concept
1. Configure a non-embedded Shopify app without setting `cookiePath` (default `'/'`).
2. In one browser, complete OAuth for `shop-a.myshopify.com`; the browser stores `shopify_app_session=<id-A>` at `path=/`.
3. In the same browser (new tab), complete OAuth for `shop-b.myshopify.com`; the browser's `Set-Cookie` for `shopify_app_session=<id-B>` at `path=/` overwrites the existing cookie.
4. Return to the `shop-a.myshopify.com` tab and make a request; `getCurrentSessionId` (`packages/apps/shopify-api/lib/session/session-utils.ts` lines 71-80) reads the cookie, returns `id-B`, and the app loads/acts using shop B's session while the user believes they are interacting with shop A.

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
