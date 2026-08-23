This is a legitimate analog in this codebase. There's a documented, real vulnerability class matching the reported bug pattern: **state overwritten silently, causing access to belong to the wrong tenant**.

### Title
Default Non-Embedded Session Cookie Path Causes Cross-Tenant Session Overwrite/Hijack - (File: `packages/apps/shopify-api/lib/auth/oauth/oauth.ts`)

### Summary
For non-embedded Shopify apps, the OAuth callback sets a single, domain-wide session cookie (`shopify_app_session`) at `path: '/'` by default. Similar to the "Merkle roots overwritten" bug class — where new state silently clobbers old state that is still relied upon — authenticating a second shop in a separate browser tab silently overwrites the first shop's session cookie, causing the browser (and thus the app) to serve/act on the wrong tenant's session for subsequent requests in the first tab.

### Finding Description
In `callback()`, the session cookie is set unconditionally at a shared path unless the app explicitly configures `cookiePath`: [1](#0-0) 

The `cookiePath` config default is `'/'` as documented directly in the config type: [2](#0-1) 

The library itself documents the exact failure mode in its own changelog: authenticating a new shop in a separate tab "silently overwrite[s] the previous shop's session," so "all tabs" then operate on "the most-recently-authenticated shop": [3](#0-2) 

When a subsequent request comes in without a `shop` query parameter (the common case for a returning tab), `getCurrentSessionId` reads whichever session id is currently present in the shared cookie — regardless of which shop that browser tab actually belongs to: [4](#0-3) 

Only `shopify-app-express`'s middleware happens to have a mitigating check that compares `session.shop` against a `shop` query param when present and redirects to auth on mismatch: [5](#0-4) 

However, this check only fires if `req.query.shop` is present; ordinary in-app navigation within an already-installed non-embedded app frequently does not carry a `shop` param, so the mismatch check is bypassed and the wrong tenant's session is used silently.

### Impact Explanation
A merchant/staff user of Shop A, who later (in the same browser, a different tab) authenticates the same app for Shop B, will have Shop A's tab silently start operating with Shop B's session cookie value. Because sessions carry `accessToken` and scope, subsequent API calls, data reads, or actions taken from "Shop A's tab" are actually executed against Shop B's tokens/session — a cross-tenant session confusion/hijack scenario, not merely a UX inconvenience. This is exactly analogous to the reported bug class: state that should be tracked per-round/per-tenant is instead a single overwritable slot, and old-but-still-valid state becomes unreachable/misattributed once overwritten.

### Likelihood Explanation
This requires no attacker action beyond ordinary usage: any store/staff member who manages multiple shops with the same non-embedded app in the same browser (a common real-world scenario for agencies/dev shops) will trigger it. The vulnerable default (`cookiePath: '/'`) is what ships out of the box; the safer per-shop path is opt-in via `cookiePath` and requires the developer to both know about the issue and restructure their app's URLs to match a shop-specific prefix, which is described as a hard requirement: [6](#0-5) 

### Recommendation
Adopt the "separate state per identity" fix already used for the Merkle-root analog: track sessions/cookies keyed uniquely per shop by default instead of relying on a single shared cookie name/path. Concretely:
- Make the session cookie name or path shop-scoped by default (not opt-in), e.g., derive a per-shop cookie name/path automatically instead of requiring app developers to configure and align `cookiePath` with their URL structure.
- In the remix/express/react-router auth strategies, always validate `session.shop` against any shop-identifying signal available (query param, referer, or an additional shop-bound cookie) before trusting a loaded session, not only when a `shop` query parameter happens to be present.

### Proof of Concept
1. Deploy a non-embedded app without setting `cookiePath` (default `'/'`).
2. In Browser Tab 1, install/authenticate the app for `shop-a.myshopify.com`. The `shopify_app_session` cookie is set to Shop A's session id at `path=/`.
3. In Browser Tab 2 (same browser), install/authenticate the app for `shop-b.myshopify.com`. Per `oauth.ts` `callback()`, the same `shopify_app_session` cookie is overwritten with Shop B's session id at the same `path=/`.
4. Return to Tab 1 and navigate to an app route that doesn't include a `shop` query parameter (e.g., a bookmarked deep link or client-side navigation). `getCurrentSessionId` reads the cookie, which now resolves to Shop B's session, and the app serves/acts using Shop B's `accessToken` in a context the user believes belongs to Shop A. [1](#0-0) [4](#0-3)

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
