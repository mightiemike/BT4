Confirmed: in `AuthCodeFlowStrategy.authenticate` (`packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts:74-96`), for non-embedded apps the `session` is loaded purely from the signed `shopify_app_session` cookie value (via `getCurrentId`/`getSessionTokenContext` in `authenticate.ts:220-226`), and the code **never checks that `session.shop === shop`** before returning `session!` as the authenticated session — unlike the equivalent `shopify-app-express` middleware (`validate-authenticated-session.ts:122-129`), which explicitly compares `session.shop !== shop` and redirects to auth on mismatch. Combined with the fact that, by default, the OAuth session cookie is written with `path: '/'` domain-wide (`base-types.ts:140-165`, `oauth.ts:219-230`), a single browser authenticating two different shops in separate tabs will have the second shop's callback silently overwrite the first shop's cookie — exactly the "overwrite" bug class from the report. In `shopify-app-remix`, because `authenticate()` doesn't re-validate `session.shop` against the request's `shop`/host, a document request in the first tab (which still shows shop A's URL/host) can end up authenticated with shop B's session and access shop B's admin data — a cross-tenant session mix-up caused by the mapping-overwrite pattern, not merely a redirect-to-reauth as in the Express package.

However, this is mitigated for embedded apps (which use JWT/App Bridge tokens, not cookies) and requires the non-embedded, `cookiePath` left at default (`/'`) configuration, and requires the victim to have authenticated two different shops in the same browser — a real but narrower precondition than a generic unauthenticated attacker.

### Title
Non-embedded admin session cookie overwrite leads to cross-tenant session confusion in `AuthCodeFlowStrategy.authenticate` - (File: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts`)

### Summary
For non-embedded apps, the OAuth session cookie (`shopify_app_session`) is written with a domain-wide `path: '/'` by default [1](#0-0) . When a user authenticates two different shops in the same browser, the second shop's OAuth callback silently overwrites the first shop's session cookie [2](#0-1) . `AuthCodeFlowStrategy.authenticate` in `shopify-app-remix` then trusts whatever session is resolved from that cookie without verifying it belongs to the shop indicated by the current request/URL [3](#0-2) .

### Finding Description
The session id used for non-embedded admin authentication is derived solely from the signed cookie via `getCurrentSessionId`, which for non-embedded apps just reads and verifies the `shopify_app_session` cookie without any shop binding check [4](#0-3) . `getSessionTokenContext` in `shopify-app-remix`'s `authenticate.ts` obtains `shop` from the URL query params and `sessionId` independently from the cookie, then passes both into the strategy [5](#0-4) . `AuthCodeFlowStrategy.authenticate` receives `{shop, session}` and only checks whether `session` exists and `session.isActive()`; it never compares `session.shop` to `shop` [3](#0-2) . This is unlike `shopify-app-express`'s `validateWithAuthCodeFlow`, which explicitly redirects to auth when `session.shop !== shop` [6](#0-5) . Because the cookie is domain-wide by default, this is directly analogous to the reported bug class: a single unscoped storage slot (cookie/mapping) that gets overwritten by the latest write, causing state meant for one context to be used in another.

### Impact Explanation
If a merchant/staff user authenticates App instance for Shop A in one tab and later Shop B in another tab (or another user of the same browser profile does so), Shop A's stale tab can end up making authenticated admin requests using Shop B's session, potentially exposing or acting on Shop B's data/token from Shop A's context (or vice versa) — a cross-tenant session confusion. The `cookiePath` config option was added specifically to mitigate this [7](#0-6) , confirming this exact overwrite class is a recognized, real vulnerability, but it is opt-in and off by default.

### Likelihood Explanation
Requires a non-embedded app configuration and a browser/user session authenticating multiple shops without setting `cookiePath` — a realistic scenario for multi-shop agencies/support staff. It does not require any credential theft, MITM, or privileged access — just normal multi-tenant usage of a non-embedded app.

### Recommendation
In `AuthCodeFlowStrategy.authenticate` (and the shared session-resolution path), verify `session.shop` matches the `shop` derived from the request before returning the session, mirroring the check already present in `shopify-app-express`. Additionally, consider making the cookie scoping (`cookiePath`) shop-aware by default for non-embedded apps rather than requiring explicit opt-in.

### Proof of Concept
1. Configure a non-embedded app with default `cookiePath` (`/'`).
2. In Browser Tab 1, complete OAuth for `shop-a.myshopify.com`; cookie `shopify_app_session` is set with `path=/`.
3. In Browser Tab 2, complete OAuth for `shop-b.myshopify.com`; the same cookie is overwritten with `shop-b`'s session id.
4. Return to Tab 1 and issue a document request without a fresh `shop` search param (as tested in `authenticate.test.ts:163-180` "loads a session from the cookie from a request with no search params when not embedded") [8](#0-7) ; `AuthCodeFlowStrategy.authenticate` returns `shop-b`'s session because it never cross-checks `session.shop` against the requested shop.

### Citations

**File:** packages/apps/shopify-api/lib/base-types.ts (L140-150)
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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts (L189-228)
```typescript
async function getSessionTokenContext(
  params: BasicParams,
  request: Request,
): Promise<SessionTokenContext> {
  const {api, config, logger} = params;

  const headerSessionToken = getSessionTokenHeader(request);
  const searchParamSessionToken = getSessionTokenFromUrlParam(request);
  const sessionToken = (headerSessionToken || searchParamSessionToken)!;

  logger.debug('Attempting to authenticate session token', {
    shop: getShopFromRequest(request),
    sessionToken: JSON.stringify({
      header: headerSessionToken,
      search: searchParamSessionToken,
    }),
  });

  if (config.isEmbeddedApp) {
    const payload = await validateSessionToken(params, request, sessionToken);
    const dest = new URL(payload.dest);
    const shop = dest.hostname;

    logger.debug('Session token is valid - authenticated', {shop, payload});
    const sessionId = config.useOnlineTokens
      ? api.session.getJwtSessionId(shop, payload.sub)
      : api.session.getOfflineId(shop);

    return {shop, payload, sessionId, sessionToken};
  }

  const url = new URL(request.url);
  const shop = url.searchParams.get('shop')!;

  const sessionId = await api.session.getCurrentId({
    isOnline: config.useOnlineTokens,
    rawRequest: request,
  });

  return {shop, sessionId, payload: undefined, sessionToken};
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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/__tests__/auth-code-flow/authenticate.test.ts (L162-180)
```typescript
  // manageAccessToken & ensureInstalledOnShop
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
