Found it. This is a directly documented, explicit design gap: the token exchange authentication strategy validates sessions using only a **local, in-memory check** (`session.isActive()` — access token presence + expiry), with no live, server-side revocation check equivalent to `hasValidAccessToken()` used in the Auth Code flow. This is the exact same bug class as the report: a control (revocation/pause) only affects *future* issuance, but a previously-issued credential (`getPrice()` analog = `authenticate()`'s trust of a locally-cached session) remains "valid" and usable by the library even after the merchant has revoked access.

### Title
Token exchange strategy trusts locally-cached session validity without live revocation check, unlike the Auth Code flow - ([File: packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts])

### Summary
The Auth Code flow's `validateAuthenticatedSession`/`ensureInstalledOnShop` middleware, when it finds an existing session, calls `hasValidAccessToken()` to make a live GraphQL request to Shopify and confirm the token has not been revoked, redirecting to re-auth on a `401` [1](#0-0) . The token-exchange based strategies (`shopify-app-remix` and `shopify-app-react-router`) skip this live check entirely: `authenticate()` only calls `session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)`, which is a purely local check for token presence and expiry, and returns the cached session immediately if it passes [2](#0-1) [3](#0-2) . `isActive()` itself is defined purely in terms of local fields (`accessToken` presence, `isExpired()`, scope match) with no network call [4](#0-3) .

### Finding Description
This is analogous to the reported bug class: a "pause"/revocation mechanism (merchant revoking app access, analogous to `setPause`) only stops *future* re-minting of credentials, but any *already-issued* credential (the analog of `getPrice()`'s stale-but-trusted value) continues to be treated as fully valid by the library for its entire natural lifetime (until `isExpired()` becomes true), because no live check equivalent to `hasValidAccessToken()`/`whenNotPaused` gate is applied on the read/authenticate path for token exchange. This is not a hypothetical: the maintainers explicitly document it as expected behavior — "Revoked (but unexpired) tokens are not re-authenticated automatically" and "If a token is revoked by the merchant before it expires, it still looks valid locally, so the library will use it" [5](#0-4) , and the same limitation is called out in the CHANGELOG [6](#0-5) .

### Impact Explanation
For apps using the `tokenExchange` future flag (the flow steered toward for new/managed-installation embedded apps), a merchant revoking the app's access does not immediately invalidate the session as seen by the library layer. `authenticate.admin()` will keep returning `admin` API clients scoped to the stale session for up to the token's remaining lifetime, and every downstream `admin` API call the app code makes will simply fail with a `401` at the HTTP layer rather than being pre-empted by the auth middleware, unless application code specifically implements retry-on-401 re-authentication (which the library does not do automatically here, unlike the Auth Code flow). If an app relies on the authenticate layer as its authorization boundary (as the Auth Code flow's live check implies is the intended contract), token-exchange apps get a materially weaker guarantee without an obvious code-level signal, since the difference is only documented in prose.

### Likelihood Explanation
This triggers on the normal, expected path of any embedded app using `future.tokenExchange: true` with a merchant that uninstalls/revokes access — no attacker action or forged request is required beyond the routine revoke-then-continue-using scenario. It is a reachable, unprivileged-actor situation for a single merchant's own shop, but the underlying credential is only usable by the same merchant, so it does not by itself grant cross-tenant access.

### Recommendation
Apply the same "self-healing" live-validation pattern used by `hasValidAccessToken()` in the Auth Code flow to the token-exchange strategy's `authenticate()`: when returning a cached session, either always perform a lightweight live check, or explicitly document/enforce that all `admin` API call sites must implement 401-triggered re-exchange (and ideally add that retry centrally in the shared `admin` client wrapper rather than leaving it to individual app code), so a revoked token cannot continue to be treated as authorized simply because it has not yet reached its `expires` timestamp.

### Proof of Concept
1. Enable `future: {tokenExchange: true}` on an embedded app using `shopify-app-remix` or `shopify-app-react-router`.
2. Complete installation via token exchange, obtaining an offline session with `accessToken` and `expires` in the future.
3. As the merchant, uninstall/revoke the app from the Shopify admin (access token is revoked server-side by Shopify, but no `APP_UNINSTALLED` webhook has yet been processed or `expires` has not passed).
4. Make a request through `shopify.authenticate.admin(request)` before the webhook is processed: `authenticate()` calls `session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)` [7](#0-6) , which returns `true` since `accessToken` is set and `expires` has not passed — the stale/revoked session is returned and used, with no live Shopify check performed, exactly mirroring `getPrice()` continuing to serve a stale value despite the asset being paused.

### Citations

**File:** packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts (L136-161)
```typescript
    if (session.isActive(api.config.scopes)) {
      config.logger.debug('Request session exists and is active', {
        shop: session.shop,
      });

      let hasValidToken: boolean;
      try {
        hasValidToken = await hasValidAccessToken(api, session);
      } catch (error) {
        config.logger.error(`Could not check if session was valid: ${error}`, {
          shop: session.shop,
        });
        hasValidToken = false;
      }

      if (hasValidToken) {
        config.logger.debug('Request session has a valid access token', {
          shop: session.shop,
        });

        res.locals.shopify = {
          ...res.locals.shopify,
          session,
        };
        return next();
      }
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts (L45-57)
```typescript
  public async authenticate(
    request: Request,
    sessionContext: SessionContext,
  ): Promise<Session> {
    const {api, config, logger} = this;
    const {shop, session, sessionToken} = sessionContext;

    if (!sessionToken) throw new InvalidJwtError();

    if (
      !session ||
      !session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)
    ) {
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts (L108-111)
```typescript
    }

    return session!;
  }
```

**File:** packages/apps/shopify-api/lib/session/session.ts (L198-206)
```typescript
  public isActive(
    scopes: AuthScopes | string | string[] | undefined,
    withinMillisecondsOfExpiry = 500,
  ): boolean {
    const hasAccessToken = Boolean(this.accessToken);
    const isTokenNotExpired = !this.isExpired(withinMillisecondsOfExpiry);
    const isScopeChanged = this.isScopeChanged(scopes);
    return !isScopeChanged && hasAccessToken && isTokenNotExpired;
  }
```

**File:** packages/apps/shopify-app-express/docs/reference/guides/token-exchange.md (L44-46)
```markdown
## Handling revoked access tokens

Token exchange reuses a stored access token while it is unexpired. If a token is **revoked** by the merchant before it expires, it still looks valid locally, so the library will use it and the Admin API call will fail with a `401`. The library does not automatically re-authenticate in that case, so your app should handle a `401` from the Admin API by re-running the request (which triggers a fresh token exchange) or re-authenticating. Expired tokens are handled automatically.
```

**File:** packages/apps/shopify-app-express/CHANGELOG.md (L114-120)
```markdown
  **What changes when enabled:**

  - `validateAuthenticatedSession` uses token exchange for embedded apps (decided once, up front: token exchange when the flag is on and the app is embedded, otherwise the Auth Code flow).
  - Fetch requests with a missing or stale session token get a `401` with the `X-Shopify-Retry-Invalid-Session-Request` header, so App Bridge fetches a fresh token and retries.
  - Document requests with a missing or stale session token render App Bridge, which fetches a fresh token and reloads. No top-level OAuth redirect.
  - The OAuth routes (`auth.begin` / `auth.callback`) are not used and return an error if called.
  - Revoked (but unexpired) tokens are not re-authenticated automatically. Handle a `401` from the Admin API by retrying the request (which triggers a fresh exchange) or re-authenticating. Expired tokens are handled automatically.
```
