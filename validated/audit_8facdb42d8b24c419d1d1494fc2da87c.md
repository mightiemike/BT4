## Finding

### Title
Non-embedded session cookie is silently overwritten across shops with no shop-mismatch validation, causing cross-tenant session confusion - (File: `packages/apps/shopify-api/lib/auth/oauth/oauth.ts`)

### Summary
This is the closest reachable analog to the "token → vault mapping can be overwritten" bug class in `shopify-app-js--016`. For non-embedded apps, the OAuth `callback` handler stores the authenticated session id in a single, domain-wide cookie (`shopify_app_session`). When a second shop completes OAuth in the same browser, this cookie is silently overwritten, re-pointing the browser's "current session" mapping to a different tenant. Unlike `shopify-app-express`, which explicitly detects this mismatch and forces re-authentication, `shopify-app-remix`'s document-request path loads and trusts the session purely from the cookie with no verification that it belongs to the shop the user believes they are acting on.

### Finding Description
`begin`/`callback` in [1](#0-0)  write the session id into a cookie named `SESSION_COOKIE_NAME` using `cookiePath ?? '/'` as the path. The `cookiePath` config comment explicitly documents the root cause: [2](#0-1)  — by default the cookie is written with `path: '/'`, so authenticating a second shop in another tab overwrites the first shop's cookie/session mapping for the whole domain. This is functionally identical to the `Manager.addToken` bug: one mapping slot (`shop → session cookie`) gets reassigned to a new value (the new shop's session id) without any cleanup or conflict check, while the old session mapping still exists (in `sessionStorage`) but becomes silently orphaned/inaccessible through the normal request path.

The CHANGELOG confirms this was a known, real collision: “authenticating a new shop would silently overwrite the previous shop's session” [3](#0-2) . The mitigation (`cookiePath` as a function of shop) is opt-in and not the default, so the vulnerable default (`path: '/'`) remains standard behavior.

Critically, `shopify-app-express`'s session validator does perform a mismatch check against the `shop` query parameter and forces re-auth if they don't match: [4](#0-3) . However, `shopify-app-remix`'s document-request path can load a session purely from the cookie with **no** `shop`/search params present at all, and still authenticates successfully using whatever session the cookie currently points to, as shown by the explicitly-tested behavior "loads a session from the cookie from a request with no search params when not embedded": [5](#0-4) . There is no equivalent shop-vs-cookie-session mismatch guard visible in the remix authenticate pipeline (`authenticate.ts`) comparable to the express middleware's check.

### Impact Explanation
A merchant/user with multiple shop tabs open (or lured into completing OAuth for an attacker-controlled/different shop in a background tab, e.g. via a crafted install link) will have their `shopify_app_session` cookie silently repointed to the new shop's session. Any subsequent same-domain, non-embedded document request made from an already-open tab for the original shop will be authenticated using the wrong tenant's session — an app-level cross-tenant session confusion. Because the remix strategy can authenticate purely off the cookie with no shop parameter to cross-check against, the mismatch is not detected and the request proceeds as "authenticated" under the incorrect shop's session/access token, unlike the express package, which would catch and reject the mismatch.

### Likelihood Explanation
Requires only unprivileged conditions: a normal merchant/user browser session interacting with an app across two different shop contexts (e.g., legitimate multi-store operation, or being tricked into an OAuth flow for another shop via a link/redirect). No secrets, MITM, or privileged access are required — the collision is a direct consequence of `path: '/'` being the default cookie scope combined with the remix package's willingness to trust the cookie alone without a shop mismatch check.

### Recommendation
- Make cookie path scoping (or an equivalent per-shop distinguishing mechanism) mandatory/default-safe rather than opt-in, e.g., default to a distinct cookie name/path derived from the shop rather than a single global cookie.
- Add a shop-vs-session mismatch check in `shopify-app-remix`'s non-embedded/document-request authentication path (mirroring `shopify-app-express`'s `session.shop !== shop` check) so that a cookie pointing to a session for an unexpected shop is rejected and forces re-authentication instead of being silently accepted.

### Proof of Concept
1. Configure a non-embedded app with default settings (`cookiePath` unset, defaults to `'/'`).
2. In Browser Tab 1, complete OAuth for `shop-a.myshopify.com`. The response sets `shopify_app_session` (and `.sig`) scoped to `path: '/'` per [1](#0-0) .
3. In Browser Tab 2 (same browser/domain), complete OAuth for `shop-b.myshopify.com`. This overwrites the same-named, same-path cookie, per the documented collision in [2](#0-1) .
4. Return to Tab 1 and issue a plain document request with no search params (as in the test scenario at [5](#0-4) ). The remix `authenticate.admin` call loads and returns `shop-b`'s session/context instead of rejecting the request, because it has no `shop` parameter to detect the mismatch (contrast with the express-side check at [4](#0-3) ).

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/__tests__/auth-code-flow/authenticate.test.ts (L163-180)
```typescript
  it('loads a session from the cookie from a request with no search params when not embedded', async () => {
    // GIVEN
    const shopify = shopifyApp(testConfig({isEmbeddedApp: false}));
    const testSession = await setUpValidSession(shopify.sessionStorage);

    // WHEN
    const request = new Request(APP_URL);
    await signRequestCookie({
      request,
      cookieName: SESSION_COOKIE_NAME,
      cookieValue: testSession.id,
    });

    const {session} = await shopify.authenticate.admin(request);

    // THEN
    expect(session).toBe(testSession);
  });
```
