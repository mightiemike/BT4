### Title
Stale pre-exchange session object invalidates and overwrites a freshly-issued offline access token, causing self-inflicted loss of session state - (File: packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts)

### Summary
In `performTokenExchange`, the session object used to "invalidate" a stale access token on a 401 error is captured *before* the new offline/online tokens are exchanged and persisted. If the failure occurs after the offline token has already been fetched and stored, the catch-block still writes back the old, pre-exchange session data (with `accessToken` cleared), clobbering the freshly stored valid session — the same root-cause pattern as the Blend `bstop_rate` bug: an update is computed/applied using a value captured *before* an intervening state change was persisted, instead of reloading/using the just-updated state.

### Finding Description
`performTokenExchange` loads the current session and immediately stashes it for later error handling: [1](#0-0) 

It then performs the offline token exchange and **stores the new session**: [2](#0-1) 

If `config.useOnlineTokens` is enabled, it goes on to exchange for an online token: [3](#0-2) 

Note that `sessionToInvalidate` is never reassigned to `offlineSession` after it is fetched and stored. If the subsequent online-token exchange (or `callAfterAuthHook`) throws an `HttpResponseError` with a 401 status, the outer catch block runs: [4](#0-3) 

`invalidateAccessToken` unconditionally overwrites the stored session using the **stale** `sessionToInvalidate` object (the pre-exchange session, which for a first-time/expired offline session may be `undefined`-token or an old record with the same session ID as the offline session that was just stored): [5](#0-4) 

Because `storeSession` implementations do a full upsert keyed by session ID (e.g. `INSERT ... ON CONFLICT (id) DO UPDATE SET` in the Postgres adapter), storing the stale object overwrites all fields of the just-persisted, valid offline session — not just the access token: [6](#0-5) 

This mirrors the audited bug class exactly: a piece of state (`bstop_rate` / here, the session record) is mutated using a value captured before a legitimate intervening update was persisted, so the update is silently lost/corrupted.

### Impact Explanation
When the online-token leg of the exchange fails with a 401 after the offline exchange already succeeded and was persisted, the app's offline session for that shop is corrupted/reverted to stale data with a cleared access token. Every subsequent authenticated request for that shop will find no valid access token and be forced back into the full re-authentication (token exchange) flow, even though a perfectly valid offline token had just been issued moments earlier. This is a self-inflicted denial of service on the authentication handler for the affected shop, triggerable purely by a transient failure on the online-token leg (e.g., Shopify returning a 401 for the online exchange due to scope/session issues), not by anything privileged.

### Likelihood Explanation
This path is reachable by any legitimate embedded-app request using token exchange with `useOnlineTokens: true` configured. It requires no attacker privilege — only a normal request flow where the offline exchange succeeds but the online exchange (or the `afterAuth` hook processing it) returns a 401. Given that 401s during online-token exchange are a realistic, non-adversarial occurrence (e.g., stale/invalid session token racing with token exchange, or Shopify-side scope changes), likelihood is moderate rather than requiring active exploitation.

### Recommendation
Update `sessionToInvalidate` to point at the most recently obtained/stored session (`offlineSession`, and then `onlineSession` if applicable) immediately after each successful `storeSession` call, so that error-path invalidation always operates on current data rather than a pre-exchange snapshot. Alternatively, reload the session by ID from storage inside the catch block before invalidating, rather than relying on a captured reference from before the mutations occurred.

### Proof of Concept
1. Configure the Express app with `future.tokenExchange: true` and `useOnlineTokens: true`, with no valid session stored for a shop.
2. Send an authenticated request with a valid session token for that shop, hitting `validateAuthenticatedSession` → `performTokenExchange`.
3. `session` loads as `undefined`; `sessionToInvalidate = undefined`.
4. The offline token exchange succeeds; `offlineSession` (with valid `accessToken`) is stored via `config.sessionStorage.storeSession(offlineSession)`.
5. The online token exchange call (`exchangeToken(..., RequestedTokenType.OnlineAccessToken)`) throws `HttpResponseError` with `response.code === 401` (simulate via a mocked 401 response from Shopify's token endpoint).
6. Catch block executes: `sessionToInvalidate` is still `undefined` in this exact sub-case (`sessionToInvalidate?.accessToken` short-circuits), so no overwrite occurs here — **but** if a *pre-existing* offline session (with an access token, e.g. one nearing expiry that triggered a re-exchange) was loaded at step 3, then `sessionToInvalidate` holds that old session object; after step 4 stores the new offline session under the same ID, the 401 in step 5 causes `invalidateAccessToken(sessionToInvalidate, config)` to store the *old* session (with `accessToken` cleared) back over the just-stored new offline session, destroying the newly issued valid credential.
7. Confirm via `sessionStorage.loadSession(offlineSessionId)` that the record no longer matches the freshly issued `offlineSession`, and that the next request forces a fresh re-authentication.

### Citations

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L74-82)
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
```

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L94-101)
```typescript
    const offlineSession = await exchangeToken(
      api,
      config,
      sessionToken,
      shop,
      RequestedTokenType.OfflineAccessToken,
    );
    await config.sessionStorage.storeSession(offlineSession);
```

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L105-116)
```typescript
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

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L150-161)
```typescript
    if (error instanceof HttpResponseError && error.response.code === 401) {
      if (sessionToInvalidate?.accessToken) {
        await invalidateAccessToken(sessionToInvalidate, config);
      }
      respondToInvalidSessionToken({
        api,
        req,
        res,
        message: error.message,
      });
      return;
    }
```

**File:** packages/apps/shopify-app-express/src/helpers/invalidate-access-token.ts (L1-12)
```typescript
import {Session} from '@shopify/shopify-api';

import {AppConfigInterface} from '../config-types';

export async function invalidateAccessToken(
  session: Session,
  config: AppConfigInterface,
): Promise<void> {
  config.logger.debug('Invalidating stale access token', {shop: session.shop});
  session.accessToken = undefined;
  await config.sessionStorage.storeSession(session);
}
```
