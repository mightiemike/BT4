### Title
Missing Concurrency Guard in Token-Exchange Session Flow Allows Reentrant Duplicate OAuth Token-Exchange Calls Before Session State is Persisted - (File: `packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts`)

### Summary
The token-exchange authentication path (used by `validateAuthenticatedSession`/`ensureInstalledOnShop` when the `tokenExchange` future flag is on, and mirrored in `shopify-app-remix` and `shopify-app-react-router`) performs a classic check-then-external-call-then-effect sequence: it reads whether a session is "active" from storage, and only *after* an asynchronous outbound call to Shopify's OAuth endpoint does it persist the new session state. Because the "is session valid" check is not re-verified nor locked against concurrent execution, any burst of concurrent requests carrying a not-yet-stored/expired session token will all pass the same stale check and each independently trigger a fresh external `POST /admin/oauth/access_token` token-exchange call, mirroring the report's "check happens before state update, external call in between" root cause.

### Finding Description
In `performTokenExchange` (`packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts:74-101`), the sequence is:
1. `session = await config.sessionStorage.loadSession(sessionId)` — **check**
2. `if (session && session.isActive(...))` — pass-through if valid
3. Otherwise, `await exchangeToken(...)` — **external interaction** (network call to Shopify's `/admin/oauth/access_token`)
4. `await config.sessionStorage.storeSession(offlineSession)` — **effect** (state update) [1](#0-0) 

There is no lock, mutex, or `idempotentPromiseHandler` guard around this check→interact→effect sequence itself. The only idempotency protection in this flow wraps the `afterAuth` hook, keyed by the raw `sessionToken` string: [2](#0-1) 

Because App Bridge issues a fresh, distinct session token for essentially every request (each with a unique `jti`), the identifier used for deduplication differs across near-simultaneous requests, so the `idempotentPromiseHandler` cannot coalesce them. Meanwhile, the storage check at the top of the handler (`session.isActive`) is evaluated once per request against session storage that has not yet been updated by any in-flight exchange, exactly analogous to `totalSupply()` not being updated before the `mintToken()` check completes in the original report.

The identical pattern is duplicated in the Remix and React Router adapters: [3](#0-2) [4](#0-3) 

### Impact Explanation
When a shop's stored offline/online session is missing or near expiry, a burst of concurrent authenticated requests (e.g., multiple embedded-app tabs/iframes reloading, or a script issuing parallel fetches) can each independently pass the stale "no valid session" check and issue their own `client_id`/`client_secret`-bearing token-exchange POST to `https://{shop}/admin/oauth/access_token`. This is a thundering-herd condition on the app's own OAuth credentials: repeated, unbounded token-exchange calls per burst can trigger Shopify's rate limiting on the endpoint, denying legitimate token refresh for that shop (denial of service of the auth handler), and unnecessarily multiplies outbound calls that leak the app's static credentials (`client_secret`) into more network requests than intended. It does not directly cause cross-tenant access, but it is a concrete, reachable availability/resource-exhaustion issue in an authentication handler stemming from the same "state check → external interaction → deferred state update" defect class described in the report.

### Likelihood Explanation
Any authenticated merchant/customer session (a single, unprivileged actor as permitted by the constraints) can trigger this without special access — simply issuing multiple concurrent requests to any route protected by `validateAuthenticatedSession`/`ensureInstalledOnShop` while their offline/online session is inactive or missing (which is common right after install, or right at token expiry under `expiringOfflineAccessTokens`). No secrets or privileged access are required, only ordinary browser-side concurrency (e.g., multiple simultaneous embedded app resource loads), making this readily reachable.

### Recommendation
Introduce a per-shop (or per-session-id) mutex/in-flight-promise cache around the entire `loadSession → exchangeToken → storeSession` sequence — not just the `afterAuth` hook — so concurrent requests for the same shop/session id await a single in-flight token exchange rather than each independently issuing a new one. This mirrors the checks-effects-interactions guidance from the original report: perform the "is a refresh already in progress" check and reservation as an atomic effect *before* making the external call, then release/update state after the interaction completes.

### Proof of Concept
1. Configure an app with `future: { tokenExchange: true }` and no stored (or an expired) offline session for a shop.
2. Fire N concurrent authenticated requests (each with its own valid but distinct App Bridge session token) to a route guarded by `shopify.validateAuthenticatedSession()`.
3. Observe that `performTokenExchange` is entered N times before any of the `storeSession` calls complete, each independently calling `api.auth.tokenExchange` and posting to `https://{shop}/admin/oauth/access_token`, as shown by the check-then-interact-then-effect code path: [5](#0-4) 
4. Repeating this burst across shops or at scale demonstrates unnecessary duplicate credentialed outbound calls that can trip Shopify-side rate limiting on the OAuth endpoint.

### Citations

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L40-51)
```typescript
async function callAfterAuthHook(
  config: AppConfigInterface,
  session: Session,
  sessionToken: string,
): Promise<void> {
  await config.idempotentPromiseHandler.handlePromise({
    promiseFunction: async () => {
      await config.hooks?.afterAuth?.({session});
    },
    identifier: sessionToken,
  });
}
```

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L74-116)
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

    logger.info('No valid session found', {shop});
    logger.info('Requesting offline access token', {shop});

    const offlineSession = await exchangeToken(
      api,
      config,
      sessionToken,
      shop,
      RequestedTokenType.OfflineAccessToken,
    );
    await config.sessionStorage.storeSession(offlineSession);

    let newSession = offlineSession;

    if (config.useOnlineTokens) {
      logger.info('Requesting online access token', {shop});
      const onlineSession = await exchangeToken(
        api,
        config,
        sessionToken,
        shop,
        RequestedTokenType.OnlineAccessToken,
      );
      await config.sessionStorage.storeSession(onlineSession);
      newSession = onlineSession;
    }
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts (L54-67)
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

      await config.sessionStorage!.storeSession(offlineSession);
```

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/admin/strategies/token-exchange.ts (L100-113)
```typescript
    if (
      !session ||
      !session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)
    ) {
      logger.info('No valid session found', {shop});
      logger.info('Requesting offline access token', {shop});
      const {session: offlineSession} = await exchangeToken({
        request,
        sessionToken,
        shop,
        requestedTokenType: RequestedTokenType.OfflineAccessToken,
      });

      await config.sessionStorage!.storeSession(offlineSession);
```
