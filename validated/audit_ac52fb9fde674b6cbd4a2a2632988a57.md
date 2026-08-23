### Title
Token-exchange `afterAuth` hook can be permanently skipped after a transient failure due to premature idempotency marking - ([File: packages/apps/shopify-app-express/src/helpers/idempotent-promise-handler.ts])

### Summary
The token-exchange authorization strategies (express, remix, react-router) treat the successful *storage* of a new access token as equivalent to *completion* of the post-auth setup step (`hooks.afterAuth`, typically used to register webhooks). The guard that is supposed to make the `afterAuth` call idempotent marks the operation as "already run" before it actually succeeds, so a single transient failure in `afterAuth` permanently prevents it from ever running again for that access token, with no path to retry — the same "premature finalization vs. incomplete execution" pattern as the Optimism bug, applied to the auth completion flow instead of a cross-domain gas budget.

### Finding Description
In `performTokenExchange` (express) and the equivalent `authenticate()` methods in the remix/react-router token-exchange strategies, the flow is:

1. Exchange the App Bridge session token for an offline (and optionally online) access token.
2. `await config.sessionStorage.storeSession(offlineSession)` — the session is now durably persisted and will be considered "active" on all future requests.
3. Call the developer-supplied `afterAuth` hook via `IdempotentPromiseHandler.handlePromise`. [1](#0-0) 

`IdempotentPromiseHandler.handlePromise` marks the `sessionToken` identifier as consumed *before* the wrapped promise (the `afterAuth` hook) has finished or succeeded: [2](#0-1) 

If `promiseFunction()` (i.e. `hooks.afterAuth`) throws — e.g. a transient network error, an Admin API rate limit while registering webhooks, or any bug in the app's own hook — the exception propagates out of `handlePromise`, and `performTokenExchange` catches it and returns a `500`: [3](#0-2) 

However, the identifier (`sessionToken`) is already recorded in `IdempotentPromiseHandler.identifiers`, so any retry with the same session token will see `isPromiseRunnable(identifier) === false` and silently skip calling `afterAuth` again — the `handlePromise` call resolves as if it succeeded. Worse, because the offline session was already persisted in step 2 *before* the failure, once the client obtains a *new* session token (after the short-lived JWT expires) and calls the endpoint again, `session.isActive(...)` is now `true`, so the whole exchange branch (including the `afterAuth` call) is skipped entirely: [4](#0-3) 

The same pattern exists in the remix and react-router packages: [5](#0-4) [6](#0-5) 

The result: for the lifetime of that access token, there is no remaining code path that will re-invoke `afterAuth` — the "guarantee" that a completed auth flow will eventually run its post-auth setup exactly once is broken in exactly the same way the Optimism report describes: partial completion (token stored) is indistinguishable from full completion (hook executed), and the retry/idempotency mechanism (`identifiers` map) locks out the retry instead of enabling it.

### Impact Explanation
`afterAuth` is commonly used to perform essential post-installation work such as registering webhooks (e.g. `APP_UNINSTALLED`, GDPR mandatory webhooks, billing checks). If it is silently and permanently skipped due to one transient failure, the merchant's install proceeds normally from the storefront/App Bridge perspective (the request ultimately looks recoverable after a retry since the session is active), but the app never completes mandatory setup for that shop — e.g. it may never register for `APP_UNINSTALLED`/GDPR webhooks, breaking compliance and data-lifecycle guarantees, or never activate billing, until the merchant fully uninstalls/reinstalls or the offline token is otherwise invalidated. This is a reliability/DoS-style failure of the auth-completion handler reachable by any single merchant during normal token exchange, not merely a contrived edge case.

### Likelihood Explanation
This does not require an attacker; any transient error inside `hooks.afterAuth` (network blip, Admin API 429/5xx during webhook registration, app bug) during a legitimate merchant's token-exchange flow triggers it. Given `afterAuth` frequently makes outbound API calls (e.g. `registerWebhooks`), transient failures are a normal occurrence in production, making this a realistically likely occurrence rather than a purely theoretical one.

### Recommendation
Do not mark the identifier as "consumed" until `promiseFunction()` has resolved successfully. In `IdempotentPromiseHandler.handlePromise`, only record the identifier (or keep it recorded) after a successful completion, and remove it (or never add it) on failure so that a subsequent retry with the same identifier will re-attempt the `afterAuth` hook. Alternatively, track success/failure state per identifier and only treat "success" as terminal, allowing failed attempts to be retried on the next request instead of relying on TTL expiry (60s) as an implicit retry window.

### Proof of Concept
1. Configure `hooks.afterAuth` to occasionally throw (simulating a transient webhook-registration failure), e.g.:
```ts
hooks: {
  afterAuth: async ({ session }) => {
    await shopify.registerWebhooks({ session }); // throws once due to rate limiting
  },
}
```
2. A merchant loads the embedded app; App Bridge supplies a session token `T1`; `performTokenExchange`/`authenticate()` exchanges it, stores the offline session, then calls `afterAuth`, which throws. The handler records `T1` in `IdempotentPromiseHandler.identifiers` and returns `500`.
3. The client retries the same request with the still-valid `T1` (JWT not yet expired). `isPromiseRunnable('T1')` returns `false`, so `afterAuth` is skipped, and the request now succeeds (session already active) — the app proceeds as though setup completed, but webhooks were never registered.
4. Even a request with a brand-new token `T2` after `T1` expires will not re-trigger `afterAuth`, because `session.isActive(...)` is `true` from the already-stored offline session, so the `!session || !session.isActive(...)` branch containing the `afterAuth` call is never entered again. [7](#0-6) [8](#0-7)

### Citations

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L65-134)
```typescript
  try {
    const payload = await api.session.decodeSessionToken(sessionToken);
    const shop = new URL(payload.dest).hostname;
    const sub = payload.sub;

    const sessionId = config.useOnlineTokens
      ? api.session.getJwtSessionId(shop, sub)
      : api.session.getOfflineId(shop);

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

    logger.debug('Request is valid, loaded session from session token', {
      shop: newSession.shop,
      isOnline: newSession.isOnline,
    });

    try {
      await callAfterAuthHook(config, newSession, sessionToken);
    } catch (error) {
      logger.error(`Error in afterAuth hook: ${error}`);
      res.status(500).send('Internal Server Error');
      return;
    }

    res.locals.shopify = {...res.locals.shopify, session: newSession};
    next();
  } catch (error) {
    if (
```

**File:** packages/apps/shopify-app-express/src/helpers/idempotent-promise-handler.ts (L1-47)
```typescript
export interface IdempotentHandlePromiseParams {
  promiseFunction: () => Promise<any>;
  identifier: string;
}

const IDENTIFIER_TTL_MS = 60000;

export class IdempotentPromiseHandler {
  protected identifiers: Map<string, number>;

  constructor() {
    this.identifiers = new Map<string, number>();
  }

  async handlePromise({
    promiseFunction,
    identifier,
  }: IdempotentHandlePromiseParams): Promise<any> {
    try {
      if (this.isPromiseRunnable(identifier)) {
        await promiseFunction();
      }
    } finally {
      this.clearStaleIdentifiers();
    }

    return Promise.resolve();
  }

  private isPromiseRunnable(identifier: string) {
    if (!this.identifiers.has(identifier)) {
      this.identifiers.set(identifier, Date.now());
      return true;
    }
    return false;
  }

  private async clearStaleIdentifiers() {
    this.identifiers.forEach((date, identifier, map) => {
      if (Date.now() - date > IDENTIFIER_TTL_MS) {
        map.delete(identifier);
      }
    });
  }
}


```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts (L45-112)
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
      logger.info('No valid session found', {shop});
      logger.info('Requesting offline access token', {shop});
      const {session: offlineSession} = await this.exchangeToken({
        request,
        sessionToken,
        shop,
        requestedTokenType: RequestedTokenType.OfflineAccessToken,
      });

      await config.sessionStorage!.storeSession(offlineSession);

      let newSession = offlineSession;

      if (config.useOnlineTokens) {
        logger.info('Requesting online access token', {shop});
        const {session: onlineSession} = await this.exchangeToken({
          request,
          sessionToken,
          shop,
          requestedTokenType: RequestedTokenType.OnlineAccessToken,
        });

        await config.sessionStorage!.storeSession(onlineSession);
        newSession = onlineSession;
      }

      logger.debug('Request is valid, loaded session from session token', {
        shop: newSession.shop,
        isOnline: newSession.isOnline,
      });

      try {
        await this.handleAfterAuthHook(
          {api, config, logger},
          newSession,
          request,
          sessionToken,
        );
      } catch (errorOrResponse) {
        if (errorOrResponse instanceof Response) {
          throw errorOrResponse;
        }

        throw new Response(undefined, {
          status: 500,
          statusText: 'Internal Server Error',
        });
      }

      return newSession;
    }

    return session!;
  }

```

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/admin/strategies/token-exchange.ts (L92-152)
```typescript
  async function authenticate(
    request: Request,
    sessionContext: SessionContext,
  ): Promise<Session> {
    const {shop, session, sessionToken} = sessionContext;

    if (!sessionToken) throw new InvalidJwtError();

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

      let newSession = offlineSession;

      if (config.useOnlineTokens) {
        logger.info('Requesting online access token', {shop});
        const {session: onlineSession} = await exchangeToken({
          request,
          sessionToken,
          shop,
          requestedTokenType: RequestedTokenType.OnlineAccessToken,
        });

        await config.sessionStorage!.storeSession(onlineSession);
        newSession = onlineSession;
      }

      logger.debug('Request is valid, loaded session from session token', {
        shop: newSession.shop,
        isOnline: newSession.isOnline,
      });

      try {
        await handleAfterAuthHook(newSession, request, sessionToken);
      } catch (errorOrResponse) {
        if (errorOrResponse instanceof Response) {
          throw errorOrResponse;
        }

        throw new Response(undefined, {
          status: 500,
          statusText: 'Internal Server Error',
        });
      }

      return newSession;
    }

    return session!;
  }
```
