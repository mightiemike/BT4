This confirms a strong, concrete analog. For non-embedded apps, `getCurrentSessionId` in [1](#0-0)  resolves the "current session" purely from a single, shop-agnostic `SESSION_COOKIE_NAME` cookie, and the OAuth callback in [2](#0-1)  writes that same cookie at a default path of `/` (`config.cookiePath ?? '/'`) unless the app opts into the newer `cookiePath` option. The CHANGELOG explicitly documents this as a pre-existing cookie-collision bug: [3](#0-2)  — "all shops shared a single `shopify_app_session` cookie at `path=/`, so authenticating a new shop would silently overwrite the previous shop's session."

### Title
Shared single-slot OAuth session cookie lets a second shop's login silently overwrite/hijack the first shop's session context in non-embedded, multi-tab apps - ([File: packages/apps/shopify-api/lib/auth/oauth/oauth.ts])

### Summary
Similar to the Angle Protocol bug where a second `disputeTree()` call overwrites the single active `disputer` slot and causes `resolveDispute()` to pay out to the wrong party, non-embedded shopify-app-js apps store the OAuth-completed session id in a single global cookie (`shopify_app_session`), scoped to `path: '/'` by default. If a second OAuth flow completes in the same browser (different tab, different shop) before the first flow's session is consumed, the shared cookie is silently overwritten, and subsequent requests resolve to the wrong shop's session.

### Finding Description
- `begin()` sets a per-flow signed OAuth `state` cookie scoped to `callbackPath` [4](#0-3) , which is fine.
- However, once `callback()` succeeds, for non-embedded apps it writes the resulting `session.id` into `SESSION_COOKIE_NAME` (`shopify_app_session`) with `path: cookiePath` which **defaults to `/`** unless the app explicitly configures `cookiePath` as a function keyed by shop: [5](#0-4) .
- `getCurrentSessionId` (used by `shopify.session.getCurrentId`, and internally by `validateAuthenticatedSession` / `authenticate.admin` for non-embedded apps) reads that same single cookie with no shop binding beyond what's inside it: [1](#0-0) .
- Because the cookie lives at `path=/` by default, it is shared across all shops/tabs for that browser+app origin. If a user (or an app open in two tabs for two different shops) completes OAuth for Shop B after already being authenticated for Shop A, Shop B's session id overwrites Shop A's cookie value. The next request in the Shop A tab now resolves to Shop B's session id.
- This is confirmed as an actual, previously-shipped defect by the project's own changelog, which introduced an **opt-in** `cookiePath` config specifically to fix it: [3](#0-2) . Because it's opt-in and defaults to `'/'`, apps that haven't adopted `cookiePath` remain vulnerable today.

### Impact Explanation
When the cookie is overwritten, `shopify.session.getCurrentId()` / `validateAuthenticatedSession` will load and act on the wrong shop's `Session` object (including its `accessToken`) for requests coming from a tab associated with a different shop. Depending on how the app renders data keyed off this session, this can lead to cross-tenant data exposure or actions being executed against the wrong merchant's store using the wrong access token — a direct analog to the disputer's funds going to the wrong party in the original bug. `shopify-app-express`'s `validateWithAuthCodeFlow` does compare `session.shop` against the `shop` query parameter and redirects to re-auth on mismatch [6](#0-5) , which mitigates the express package specifically, but the underlying `shopify-api` primitives (`getCurrentSessionId`, `auth.callback`) do not enforce this by default, and any code path or app that calls `getCurrentId`/loads sessions without that shop check is exposed.

### Likelihood Explanation
Requires either (a) a normal user with multiple shops/tabs open simultaneously against the same non-embedded app that hasn't set `cookiePath`, or (b) an attacker luring a merchant into completing an attacker-controlled shop's install/OAuth flow (e.g., via a link) while the victim has an active session for their real shop in another tab of the same browser — a realistic griefing/confusion scenario requiring no more privilege than "any user/merchant able to trigger `auth.begin`/`auth.callback` for an arbitrary shop," directly mirroring the "second unprivileged disputer overwrites the first" pattern.

### Recommendation
Make shop-scoped `cookiePath` (or otherwise namespacing the session cookie name/value per shop) the default behavior rather than an opt-in future flag, and add a shop-consistency check inside `shopify-api`'s `getCurrentSessionId`/`callback` itself, not just as a downstream integration's responsibility (as `shopify-app-express` currently does), so the fix isn't dependent on each consuming framework repeating the mitigation.

### Proof of Concept
1. Configure a non-embedded app without setting `cookiePath` (default `'/'`).
2. In Browser Tab 1, complete OAuth for `shop-a.myshopify.com`; the browser stores `shopify_app_session=<session-a-id>` at `path=/`.
3. In Browser Tab 2 (same browser), complete OAuth for `shop-b.myshopify.com` (e.g., by following an app-install link for a different/attacker shop). This overwrites the cookie: `shopify_app_session=<session-b-id>`.
4. Return to Tab 1 and issue a request; `shopify.session.getCurrentId()` now returns `<session-b-id>`, so the app loads Shop B's `Session`/access token in a context the user believes is Shop A — see `getCurrentSessionId` reading the single shared cookie at [7](#0-6) .

### Citations

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

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L88-100)
```typescript
    const cookies = new Cookies(request, response, {
      keys: [config.apiSecretKey],
      secure: true,
    });

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
