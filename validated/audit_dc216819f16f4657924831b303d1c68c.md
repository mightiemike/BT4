Based on my analysis, I found a valid analog: the `SessionStorage.storeSession()` return value—explicitly typed to signal success/failure—is silently discarded across multiple critical OAuth completion paths, exactly mirroring the unchecked `approve()` pattern in the original report (a boolean success signal from a critical state-changing operation is ignored, and execution proceeds as if it succeeded).

### Title
Unchecked `storeSession()` return value causes silent session-persistence failure and permanent OAuth lockout - (File: `packages/apps/shopify-app-express/src/auth/auth-callback.ts`)

### Summary
`SessionStorage.storeSession()` is explicitly typed to return `Promise<boolean>` so callers can detect persistence failure, but every OAuth-callback completion path in `shopify-app-express`, `shopify-app-remix`, and `shopify-app-react-router` ignores this return value, treating the session as durably stored regardless of the outcome.

### Finding Description
The `SessionStorage` interface declares `storeSession(session: Session): Promise<boolean>` where `false` indicates the write did not succeed [1](#0-0) . Several concrete adapters actually use this contract to signal failure — e.g. `DrizzleSessionStorageMySQL.deleteSession`/`deleteSessions` return `false` on error [2](#0-1) , and the maintainers' own migration guide explicitly shows checking `storeSession()`'s return value and logging a failure when it is `false` [3](#0-2) .

Despite this documented contract, the production OAuth-callback handlers never check it:

- In `shopify-app-express`, `authCallback()` calls `await config.sessionStorage.storeSession(callbackResponse.session);` without checking the result, then unconditionally proceeds to register webhooks, sets `res.locals.shopify.session`, and fires the `afterAuth` hook as if the session were durably persisted [4](#0-3) .
- In `shopify-app-remix`'s `AuthCodeFlowStrategy.handleAuthCallbackRequest()`, the same pattern occurs: `await config.sessionStorage!.storeSession(session);` is unchecked, followed immediately by triggering the `afterAuth` hook and redirecting the merchant into the app as if authentication/session persistence fully succeeded [5](#0-4) .
- The identical unchecked pattern exists in `shopify-app-react-router`'s equivalent strategy file, and in the token-exchange middleware (`performTokenExchange`) for both offline and online token storage calls.

### Impact Explanation
If the storage backend returns `false` (e.g., a transient DB write failure, a race/conflict, or a non-standard adapter that fails without throwing — directly analogous to ERC20 tokens returning `false` instead of reverting), the app:
1. Believes OAuth completed successfully and redirects the merchant into the embedded app / fires `afterAuth` hooks.
2. Has no record of the session in storage, so the very next request cannot find a valid session and re-triggers the OAuth flow.
3. Provides no error path, logging, or retry — the failure is completely invisible to the merchant and to app operators, since the code path treats `storeSession` exactly like a fire-and-forget side effect.

This is a denial-of-service of the auth handler for the affected shop: the merchant can become stuck in a repeated OAuth loop with no diagnostic trail, and in offline-token flows this could also cause webhook registration or `afterAuth` business logic (e.g., billing setup, provisioning) to run against a session that will vanish immediately after the request completes, potentially causing inconsistent per-shop state.

### Likelihood Explanation
This path is reachable by any merchant simply installing or re-authenticating the app (an unprivileged, single-merchant OAuth callback request) — no attacker capability beyond normal app installation is required. It manifests whenever the configured `SessionStorage` implementation can return `false` (as its own interface and several first-party adapters do) or otherwise fail without throwing, which is a realistic operational condition (e.g., DB constraint violations, transient connectivity issues, custom adapters).

### Recommendation
Check the boolean result of every `storeSession()` call in the OAuth-callback and token-exchange paths (`auth-callback.ts`, `auth-code-flow.ts` in both remix and react-router packages, `perform-token-exchange.ts`, and the token-exchange strategy files). On `false`, abort the flow, log/alert the failure, and return an error response (e.g., 500) instead of proceeding to register webhooks, set `res.locals.shopify.session`, fire `afterAuth`, or redirect into the app — mirroring how the maintainers' own migration doc demonstrates checking this value.

### Proof of Concept
1. Configure a custom `SessionStorage` implementation whose `storeSession()` returns `false` on write conflicts (a legitimate implementation of the documented interface, not a broken one).
2. Trigger app installation/OAuth for a shop; force the storage write to fail (e.g., simulate a DB constraint violation) so `storeSession()` resolves to `false`.
3. Observe that `authCallback()` (or `AuthCodeFlowStrategy.handleAuthCallbackRequest()`) still proceeds to fire `afterAuth`, sets `res.locals.shopify.session`, and redirects the merchant into the app — with no error surfaced.
4. On the merchant's very next request, `sessionStorage.loadSession()` returns `undefined` since nothing was actually persisted, forcing the app back into the OAuth flow — an unrecoverable loop as long as the underlying write condition persists, with zero visibility into the root cause.

### Citations

**File:** packages/apps/session-storage/shopify-app-session-storage/src/types.ts (L6-12)
```typescript
export interface SessionStorage {
  /**
   * Creates or updates the given session in storage.
   *
   * @param session Session to store
   */
  storeSession(session: Session): Promise<boolean>;
```

**File:** packages/apps/session-storage/shopify-app-session-storage-drizzle/src/adapters/drizzle-mysql.adapter.ts (L60-86)
```typescript
  public async deleteSession(id: string): Promise<boolean> {
    try {
      await this.db
        .delete(this.sessionTable)
        .where(eq(this.sessionTable.id, id));
    } catch (error) {
      console.error(error);

      return false;
    }

    return true;
  }

  public async deleteSessions(ids: string[]): Promise<boolean> {
    try {
      await this.db
        .delete(this.sessionTable)
        .where(inArray(this.sessionTable.id, ids));

      return true;
    } catch (error) {
      console.error(error);

      return false;
    }
  }
```

**File:** packages/apps/shopify-api/docs/example-migration-v5-node-template-to-v6.md (L246-249)
```markdown
+      // save the session
+      if ((await sqliteSessionStorage.storeSession(callbackResponse.session)) == false) {
+        console.log(`Failed to store session ${callbackResponse.session.id}`);
+      }
```

**File:** packages/apps/shopify-app-express/src/auth/auth-callback.ts (L33-61)
```typescript
    await config.sessionStorage.storeSession(callbackResponse.session);

    // If this is an offline OAuth process, register webhooks
    if (!callbackResponse.session.isOnline) {
      await registerWebhooks(config, api, callbackResponse.session);
    }

    // If we're completing an offline OAuth process, immediately kick off the online one
    if (config.useOnlineTokens && !callbackResponse.session.isOnline) {
      config.logger.debug(
        'Completing offline token OAuth, redirecting to online token OAuth',
        {shop: callbackResponse.session.shop},
      );

      await redirectToAuth({req, res, api, config, isOnline: true});
      return false;
    }

    res.locals.shopify = {
      ...res.locals.shopify,
      session: callbackResponse.session,
    };

    await config.hooks?.afterAuth?.({session: callbackResponse.session});

    config.logger.debug('Completed OAuth callback', {
      shop: callbackResponse.session.shop,
      isOnline: callbackResponse.session.isOnline,
    });
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts (L197-217)
```typescript
      await config.sessionStorage!.storeSession(session);

      if (config.useOnlineTokens && !session.isOnline) {
        logger.info('Requesting online access token for offline session', {
          shop,
        });
        await beginAuth({api, config, logger}, request, true, shop);
      }

      logger.debug('Request is valid, loaded session from OAuth callback', {
        shop: session.shop,
        isOnline: session.isOnline,
      });

      await triggerAfterAuthHook({api, config, logger}, session, request, this);

      throw await redirectToShopifyOrAppRoot(
        request,
        {api, config, logger},
        responseHeaders,
      );
```
