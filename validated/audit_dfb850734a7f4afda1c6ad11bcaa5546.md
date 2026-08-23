Confirmed: this is a real gap. In `AuthCodeFlowStrategy.authenticate` (`packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts:74-96`), the method only checks whether `session` exists and `session.isActive(config.scopes)` — it never compares `session.shop` to the `shop` value that was passed in via `sessionContext.shop`. For non-embedded apps, that `shop` comes straight from the URL query parameter (`getSessionTokenContext`, `packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts:220-228`), while `session` is loaded independently via `sessionId` from `api.session.getCurrentId` (signed cookie). Because `session.shop` is trusted implicitly and never cross-checked against the untrusted `shop` query parameter here (unlike `validateWithAuthCodeFlow` in the Express package, which explicitly does `if (session && shop && session.shop !== shop) { return redirectToAuth(...) }` — `packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts:122-129`), the Remix `AuthCodeFlowStrategy` lacks that same tenant-consistency check.

This mirrors the report's root cause exactly: a resource-identifying parameter (`_taskId` / here `shop`) is accepted and used downstream without validating it matches the actual owning entity (`dcaId` / here the session's true shop), because the check is missing in one place but present in an analogous sibling implementation.

### Title
Missing shop/session consistency check in Remix `AuthCodeFlowStrategy.authenticate` - (File: packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts)

### Summary
`AuthCodeFlowStrategy.authenticate` validates only that a `Session` exists and is active; it never verifies that `session.shop` matches the `shop` value supplied in the request context, unlike the equivalent Express middleware which performs this exact check.

### Finding Description
`getSessionTokenContext` (packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts:220-228) derives `shop` from the raw `url.searchParams.get('shop')` query parameter for non-embedded requests, and separately derives `sessionId` from `api.session.getCurrentId`, then loads `existingSession` from storage by that id (authenticate.ts:159-173). Both values are passed into `strategy.authenticate(request, {session, sessionToken, shop})`. [1](#0-0) [2](#0-1) 

Inside `AuthCodeFlowStrategy.authenticate`, the code checks only `!session` and `!session.isActive(...)`; it returns `session!` without ever comparing `session.shop` to the `shop` field carried in `sessionContext`. [3](#0-2) 

The Express package's equivalent function performs the missing check explicitly, redirecting to auth if `session.shop !== shop`: [4](#0-3) 

This is analogous to the reported `stopDca` bug: an identifier tied to one entity (task id / shop) is accepted and acted upon without confirming it belongs to the resource actually being operated on (DCA / session).

### Impact Explanation
If session storage or downstream consumers key any behavior off the `shop` value returned by `authenticateAdmin` (e.g., logging, redirect targets, CORS/host validation, or app logic reading `context.session` alongside a `shop` param elsewhere) without re-deriving it strictly from `session.shop`, a mismatch between an attacker-supplied `shop` query parameter and the actual authenticated session's shop is not surfaced/rejected at this layer. This could enable confusion between the requested tenant and the actually-authenticated tenant in non-embedded flows.

### Likelihood Explanation
The `shop` query parameter is fully attacker-controlled and requires no privilege; a non-embedded app request with a valid cookie session but a different `shop` query string reaches `authenticate()` without a mismatch check. Likelihood of exploitation depends on whether any code downstream relies on the unchecked `shop` field rather than `session.shop`.

### Recommendation
- Short term: Add the same `session.shop !== shop` check in `AuthCodeFlowStrategy.authenticate` (or in `getSessionTokenContext`/`authenticateAdmin`) that exists in the Express `validateWithAuthCodeFlow`, redirecting to OAuth/exit-iframe on mismatch.
- Long term: Consolidate shop/session consistency validation into a single shared helper used by all framework packages (Express, Remix, React Router) so this check cannot be silently omitted in one implementation, and add regression tests asserting a mismatched `shop` query param triggers re-authentication rather than being silently accepted.

### Proof of Concept
1. A merchant with shop `victim.myshopify.io` has a valid non-embedded session cookie (`shopify_app_session`) issued by the app.
2. An attacker (or a crafted link) causes the browser to hit `GET /app/whatever?shop=attacker.myshopify.io` while the victim's cookie is still valid/sent.
3. `getSessionTokenContext` sets `shop = "attacker.myshopify.io"` from the query string, but `sessionId`/`existingSession` resolve to the victim's real session for `victim.myshopify.io`.
4. `AuthCodeFlowStrategy.authenticate` only checks `session.isActive()`, not `session.shop === shop`, and returns the victim's session paired with the attacker-supplied `shop` value in the returned context—no redirect/rejection occurs at this layer as it does in the Express equivalent.

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts (L159-175)
```typescript
      const {payload, shop, sessionId, sessionToken} =
        await getSessionTokenContext(params, request);

      logger.info('Authenticating admin request', {shop});

      logger.debug('Loading session from storage', {shop, sessionId});
      const existingSession = sessionId
        ? await config.sessionStorage!.loadSession(sessionId)
        : undefined;

      const session = await strategy.authenticate(request, {
        session: existingSession,
        sessionToken,
        shop,
      });

      return createContext(request, session, strategy, payload);
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

**File:** packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts (L122-129)
```typescript
  if (session && shop && session.shop !== shop) {
    config.logger.debug('Found a session for a different shop in the request', {
      currentShop: session.shop,
      requestShop: shop,
    });

    return redirectToAuth({req, res, api, config});
  }
```
