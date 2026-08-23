### Title
Non-embedded multi-shop OAuth session cookie collision leads to cross-tenant session confusion - ([File: packages/apps/shopify-api/lib/auth/oauth/oauth.ts])

### Summary
For non-embedded Shopify apps, the OAuth callback writes the `shopify_app_session` cookie (`SESSION_COOKIE_NAME`) using `session.id` as its value, at a path controlled by the optional `config.cookiePath` setting, which **defaults to `/`** when unset. [1](#0-0) 
This mirrors the reported bug class: a keyed store (here, a single domain-wide cookie) is silently overwritten by a second write intended for a different logical entity (a different shop), while the previous entity's association (session mapping via the browser) is lost/confused rather than being kept isolated — analogous to the Vesting contract's `schedules[beneficiary]` being overwritten by a second schedule for the same key.

### Finding Description
The `callback` function in `oauth.ts` sets the session cookie like this: [1](#0-0) 
Because `cookiePath` defaults to `'/'` (domain-wide) when not explicitly configured, one browser cookie jar (per app domain) holds only one `shopify_app_session` value at a time. If the same user authenticates a second, different shop in another tab/window of the same browser (e.g., an agency/dev managing multiple stores, or a phished/redirected flow to a second store during an active session on the first), the second shop's OAuth callback overwrites the cookie value that was pointing to the first shop's session id. The library itself documents this exact defect: [2](#0-1) 
and the same is echoed in the config type's own doc comment: [3](#0-2) 
`getCurrentSessionId`/`shopify.session.getCurrentId` simply trusts and returns whatever session id is present in that single cookie for non-embedded apps, with no shop binding built into the cookie itself: [4](#0-3) 
Whether this results in an actual account/session mix-up depends entirely on the downstream framework package performing (or not performing) a `session.shop !== requestedShop` check before using the loaded session:
- `shopify-app-express`'s `validateAuthenticatedSession` middleware *does* perform this check and redirects to re-auth on mismatch: [5](#0-4) 
- I could not confirm an equivalent explicit `session.shop !== shop` guard in `shopify-app-remix`'s non-embedded `AuthCodeFlowStrategy.authenticate` path within the code I was able to inspect; it retrieves `sessionId`/`session` via `getSessionTokenContext`/`getCurrentId` and proceeds to treat it as valid if `session.isActive()`, without an explicit shop-equality re-check visible in `auth-code-flow.ts`: [6](#0-5) [7](#0-6) 

### Impact Explanation
If a downstream package (or a custom implementation built directly on `@shopify/shopify-api`, bypassing `shopify-app-express`'s shop-match guard) does not itself verify that the loaded session's `shop` matches the shop being requested/rendered, a merchant/user operating multiple shops from the same browser could have their non-embedded app requests silently served with another shop's session (a cross-tenant session confusion), because the shared `shopify_app_session` cookie always points to only the most recently authenticated shop. This is a data/session integrity issue rather than a directly attacker-forged token, and its severity depends on the consuming app/framework's own validation — which is why `shopify-app-express` explicitly guards against it while the library's core primitive (`oauth.ts` + `session-utils.ts`) does not enforce shop binding by default.

### Likelihood Explanation
Medium-low: it requires (a) a non-embedded app, (b) `cookiePath` left at its default `'/'` (the documented default, not opt-in-safe), and (c) the same user/browser authenticating more than one shop for that app. This is a realistic scenario for agencies, developers, or multi-store operators using non-embedded apps, and requires no attacker action beyond normal multi-shop usage or being induced to open an auth link for a second shop while a session for the first is active — comparable to the "requires more than one schedule for the same beneficiary" medium-likelihood characterization in the original report.

### Recommendation
- Ship a safe default that prevents domain-wide cookie collisions instead of relying on developers to opt into `cookiePath` [8](#0-7) , e.g. by deriving a shop-scoped cookie name or default path automatically.
- Ensure every downstream consumer of `session.session.getCurrentId`/loaded sessions (not just `shopify-app-express`) enforces `session.shop === requestedShop` before treating a session as valid, mirroring the check already present in `validate-authenticated-session.ts`.

### Proof of Concept
1. Deploy a non-embedded app without setting `cookiePath` (default `'/'`).
2. In one browser, complete OAuth for `shop-a.myshopify.com`; the app sets `shopify_app_session=<session-id-A>` at path `/`.
3. In a new tab of the same browser, complete OAuth for `shop-b.myshopify.com`; per `oauth.ts` lines 219-230, this overwrites the same cookie with `<session-id-B>` at path `/`.
4. Return to the first tab and make a request under `shop-a`'s context; `getCurrentSessionId` (`session-utils.ts` lines 71-80) returns `<session-id-B>` because it is the only cookie value present, regardless of the intended shop.
5. If the consuming app/framework does not explicitly re-check `session.shop` against the requested shop before use (as `shopify-app-express` does but which I could not confirm equivalently in `shopify-app-remix`'s `AuthCodeFlowStrategy`), the request proceeds using shop B's session/access token while the user believes they are interacting with shop A.

Note: I was not able to fully verify, within the indexed portion of the codebase, whether `shopify-app-remix`/`shopify-app-react-router` perform an equivalent shop-match guard elsewhere in their request pipeline (e.g., in route loaders or a shared helper not surfaced by my searches). This should be confirmed with direct access to the full `shopify-app-remix` and `shopify-app-react-router` authenticate pipelines before treating this as a confirmed end-to-end exploit versus a defense-in-depth gap in the core `shopify-api` package.

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
