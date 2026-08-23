### Title
Non-embedded app session cookie is not scoped per shop by default, allowing cross-tenant session cookie collision - (File: `packages/apps/shopify-api/lib/auth/oauth/oauth.ts`)

### Summary
For non-embedded apps, the OAuth callback sets a single `shopify_app_session` cookie whose path defaults to `/` for every shop the app serves. If the same browser completes OAuth for two different shops (a realistic scenario for agencies/developers managing multiple installs, or any shared browser context), the cookie set for the second shop silently overwrites the cookie for the first, similar in spirit to the reported bug class where an unscoped shared value can be claimed/overwritten by whichever party interacts with it first/last, benefiting or harming an unrelated party.

### Finding Description
In `begin`/`callback` of `packages/apps/shopify-api/lib/auth/oauth/oauth.ts`, the session cookie is written with a configurable `cookiePath`, but it defaults to `'/'` for all shops: [1](#0-0) 

The project's own changelog confirms this is a known collision: all shops previously shared a single `shopify_app_session` cookie at `path=/`, so authenticating a new shop silently overwrites the previous shop's cookie/session, and this is only fixed by opting in to a new `cookiePath` config (default behaviour is explicitly documented as "unchanged"): [2](#0-1) 

Downstream, `packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts` reads the cookie-derived `sessionId`, loads the session from storage, and only rejects a shop/session mismatch when a `shop` query parameter is present on the request; if it isn't, it falls back to trusting `session.shop` from whatever session the (possibly overwritten) cookie points to: [3](#0-2) 

So the chain is: (1) cookie is shared across shops by default (`cookiePath` defaults to `/`), (2) the mismatch guard is only exercised when a `shop` query param happens to be present, otherwise the loaded (possibly wrong-tenant) session is accepted as-is.

### Impact Explanation
An unprivileged actor does not need to forge any token to trigger this — it only requires normal use of the app across two shop installs from the same browser/session-cookie jar (e.g., a merchant or agency managing multiple stores, or shared browsers in support/testing environments). The result can be that requests intended for shop A are served using the session (and therefore access token / data) belonging to shop B, or vice versa, on any route that doesn't explicitly carry a `shop` query parameter. This is a cross-tenant session confusion issue rather than a classic authentication bypass, so its severity depends heavily on how strictly each app route enforces the `shop` parameter.

### Likelihood Explanation
Requires a non-embedded app, default (un-set) `cookiePath` configuration, and a single browser cookie jar interacting with more than one shop's OAuth flow for the same app — a plausible but not universal scenario. The maintainers themselves flagged and partially mitigated it (opt-in fix), which indicates it is a real, previously-unaddressed-by-default condition rather than a theoretical one.

### Recommendation
- Make shop-scoped `cookiePath` the default behavior (or otherwise namespace the session cookie name/path by shop) rather than requiring apps to opt in via configuration.
- In `validate-authenticated-session.ts` (and analogous logic in `shopify-app-remix`/`shopify-app-react-router`), always verify `session.shop` against an explicitly derived `shop` for the request — never fall back silently to trusting `session.shop` alone when no `shop` parameter is present — for non-embedded flows, derive/require the shop from the `host`/referrer or force re-authentication when it cannot be determined.

### Proof of Concept
1. Configure a non-embedded app with default `cookiePath` (`'/'`).
2. From the same browser, complete OAuth for `shop-a.myshopify.com`; the `shopify_app_session` cookie is set at path `/` for `shop-a`'s session id.
3. In the same browser, complete OAuth for `shop-b.myshopify.com`; the cookie at path `/` is overwritten with `shop-b`'s session id.
4. Return to a previously-opened tab for `shop-a` and hit a route that does not include an explicit `?shop=` query parameter; `validate-authenticated-session.ts` loads the session using the (now shop-b) cookie value, and since no `shop` param is present to trigger the mismatch redirect, the request proceeds using shop-b's session in a context the user believes belongs to shop-a.

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
