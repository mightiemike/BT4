### Title
Token-exchange session validation trusts locally cached expiry only, so a revoked access token is treated as valid until natural expiry - (File: packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts)

### Summary
This is a structural analog of the ParaSpace `NFTFloorOracle` bug: `setPause()` (revocation) only blocks *future* `setPrice()` calls, while `getPrice()` (the read/validity check) keeps trusting the last cached value regardless of the pause flag. In shopify-app-js's token-exchange authentication strategies, the equivalent "read/validity check" is `Session.isActive()`, which only inspects the locally stored `expires` timestamp and scopes — it never re-verifies with Shopify that the access token hasn't been revoked. Revocation (the "pause") therefore only affects *future* token-exchange calls; it does nothing to the already-issued, already-stored access token, which keeps being served to app code as valid for its entire (sometimes long) TTL.

### Finding Description
`Session.isActive()` determines validity purely from local state: [1](#0-0) 

All three token-exchange authorization strategies (`shopify-app-remix`, `shopify-app-react-router`, `shopify-app-express`) use exactly this local check to decide whether to reuse a stored session's access token, without any live call to Shopify to confirm the token is still valid: [2](#0-1) [3](#0-2) 

This is explicitly acknowledged in the library's own documentation as a known behavior — i.e., it is not a hypothetical: [4](#0-3) 

By contrast, the legacy Auth Code flow middleware (`validateWithAuthCodeFlow`) does perform a live check by making an actual GraphQL request via `hasValidAccessToken()` before trusting the session: [5](#0-4) [6](#0-5) 

So the codebase already contains the "correct" pattern (live revalidation) for one auth strategy, but the token-exchange strategies — which are the ones Shopify recommends for new embedded apps — omit it and rely solely on the cached expiry, mirroring the `getPrice()`/`setPause()` asymmetry: the "pause" (revocation) event updates nothing that the read path consults.

### Impact Explanation
When a merchant (or Shopify, e.g. due to a billing/compliance/fraud action) revokes an access token before its `expires` timestamp, apps using token exchange (the now-recommended, Shopify-managed-installation flow) will continue to serve that token from session storage as "active" for every request until:
1. it naturally expires, or
2. an outbound Admin API call happens to fail with 401 (and even then, the framework does not proactively re-authenticate — the app must handle the 401 itself, per the documented caveat).

This creates a window where the app keeps operating (registering webhooks, running scheduled jobs, serving authenticated admin requests) on behalf of a shop whose authorization was supposed to have been terminated, exactly analogous to the oracle continuing to return the pre-lockdown price for up to 6 hours after `setPause()`.

### Likelihood Explanation
This triggers under normal, expected merchant action (revoking access / reinstalling / rotating scopes) with no attacker interaction required — it's the default behavior of the shipped, recommended token-exchange strategy, so it will occur whenever a token is revoked mid-lifetime. Given expiring offline/online tokens can have TTLs up to the token's granted lifetime, the exposure window is not necessarily short.

### Recommendation
Mirror the Auth Code flow's `hasValidAccessToken()` live-check in the token-exchange strategies (`shopify-app-remix`, `shopify-app-react-router`, `shopify-app-express`'s `performTokenExchange`) — or at minimum, add an active-token verification step (e.g., a lightweight authenticated request) before reusing a cached session, rather than relying exclusively on `Session.isActive()`'s local expiry/scope check. At the very least, document this as a required app-level mitigation loudly in each package's guide, not only in `shopify-app-express`.

### Proof of Concept
1. Merchant installs app via token exchange; app stores an offline session with `expires` far in the future.
2. Merchant revokes the app's access (e.g., via the Partner/merchant admin) before that expiry.
3. Any subsequent authenticated request (`shopify.authenticate.admin`) calls `session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)`, which returns `true` because `expires` hasn't passed — the stale, revoked token is handed to app code and used for Admin API calls until it happens to fail with a 401, per [2](#0-1) .

### Citations

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts (L54-66)
```typescript
    if (
      !session ||
      !session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)
    ) {
      logger.info('No valid session found', {shop});
      logger.info('Requesting offline access token', {shop});
      const {session: offlineSession} = await this.exchangeToken({
        request,
        sessionToken,
        shop,
        requestedTokenType: RequestedTokenType.OfflineAccessToken,
      });

```

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L74-89)
```typescript
    let session: Session | undefined;
    try {
      session = await config.sessionStorage.loadSession(sessionId);
      sessionToInvalidate = session;
    } catch (error) {
      logger.error(`Error when loading session from storage: ${error}`);
      res.status(500).send('Internal Server Error');
      return;
    }

    if (session && session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)) {
      logger.debug('Request is valid, session is active', {shop: session.shop});
      res.locals.shopify = {...res.locals.shopify, session};
      next();
      return;
    }
```

**File:** packages/apps/shopify-app-express/docs/reference/guides/token-exchange.md (L44-46)
```markdown
## Handling revoked access tokens

Token exchange reuses a stored access token while it is unexpired. If a token is **revoked** by the merchant before it expires, it still looks valid locally, so the library will use it and the Admin API call will fail with a `401`. The library does not automatically re-authenticate in that case, so your app should handle a `401` from the Admin API by re-running the request (which triggers a fresh token exchange) or re-authenticating. Expired tokens are handled automatically.
```

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

**File:** packages/apps/shopify-app-express/src/middlewares/has-valid-access-token.ts (L9-25)
```typescript
export async function hasValidAccessToken(
  api: Shopify,
  session: Session,
): Promise<boolean> {
  try {
    const client = new api.clients.Graphql({session});
    await client.request(TEST_GRAPHQL_QUERY);
    return true;
  } catch (error) {
    if (error instanceof HttpResponseError && error.response.code === 401) {
      // Re-authenticate if we get a 401 response
      return false;
    } else {
      throw error;
    }
  }
}
```
