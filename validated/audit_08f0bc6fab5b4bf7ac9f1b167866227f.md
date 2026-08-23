## Finding: Premature State Update in `IdempotentPromiseHandler` Silently Suppresses `afterAuth` Hook Retries

### Title
Idempotency Identifier Marked "Used" Before Promise Resolution Silently Skips Failed `afterAuth` Hook Retries - (File: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/idempotent-promise-handler.ts`, also duplicated in `shopify-app-react-router` and `shopify-app-express`)

### Summary
`IdempotentPromiseHandler.handlePromise()` marks an identifier (the session token) as "already run" *before* the wrapped `promiseFunction` actually completes successfully. This mirrors the reported `batchRelease()` bug class: a state mutation is committed before the corresponding side effect is verified to have succeeded, so a legitimate retry silently no-ops instead of re-attempting the effect.

### Finding Description
`isPromiseRunnable()` inserts the identifier into the `identifiers` map on first sight, then `handlePromise()` awaits `promiseFunction()` inside the same `try` block: [1](#0-0) 

If `promiseFunction()` throws (e.g. a transient failure while registering webhooks in the `afterAuth` hook), the identifier was already added to the map, and `clearStaleIdentifiers()` only evicts entries older than the 60s TTL — it does not remove the just-failed one. This is confirmed by the existing unit test showing the entry survives a rejection: [2](#0-1) 

This handler is used to gate the `afterAuth` hook during the token-exchange authentication flow, keyed by the raw session token supplied by the (App Bridge) client on every request: [3](#0-2) 

The same pattern exists in `shopify-app-express`'s `performTokenExchange`, where `callAfterAuthHook` is keyed by `sessionToken` and any error inside the hook is swallowed as a 500 on the *first* call, but a retry with the same still-valid session token is treated as a no-op success: [4](#0-3) 

Because `afterAuth` is documented as the place apps register webhooks (per `token-exchange.md`), a failed webhook registration on the first token-exchange call becomes permanently unrecoverable for the life of that session token (up to the 60s TTL / token validity window), even though the surrounding request-handling code returns success on retry. [5](#0-4) 

### Impact Explanation
This is a request-authentication-handler reliability/DoS issue: the "done" state (identifier presence in the map) is committed before the actual side effect (the `afterAuth` hook, e.g. webhook registration) is confirmed to have succeeded, exactly the checks-effects-interactions inversion described in the source report. A single merchant's app session hitting a transient failure during `afterAuth` will have all subsequent retries within the TTL window silently short-circuited, masking the failure and preventing webhook registration or other hook-driven security/consistency setup from ever completing for that identifier — without any error being surfaced to the caller on retry.

### Likelihood Explanation
Reachable by any authenticated embedded-app request going through the token-exchange path (no elevated privileges required beyond a normal merchant/session), triggered whenever the `afterAuth` hook throws once (e.g. transient network error, webhook API rate limit, etc.), which is a realistic operational condition rather than a contrived edge case.

### Recommendation
Only mark the identifier as "used" (or move it to a completed set) after `promiseFunction()` resolves successfully; on rejection, remove the identifier immediately so a subsequent retry with the same identifier can re-attempt the hook, matching the source report's recommendation to combine the state update and the effect atomically instead of separating "mark done" from "do the work."

### Proof of Concept
Using the existing test harness pattern:
```ts
const promiseHandler = new IdempotentPromiseHandler();
let attempt = 0;
const promiseFunction = async () => {
  attempt++;
  if (attempt === 1) throw new Error('transient webhook registration failure');
};

await expect(
  promiseHandler.handlePromise({promiseFunction, identifier: 'token-abc'}),
).rejects.toThrow();

// Retry with the same identifier (e.g. same still-valid session token)
await promiseHandler.handlePromise({promiseFunction, identifier: 'token-abc'});

// attempt is still 1 — the retry silently skipped re-running afterAuth logic
expect(attempt).toBe(1);
```
This demonstrates that after an initial failure, `afterAuth` (and whatever webhook registration or setup it performs) never actually re-runs on retry with the same session token, even though the outer request flow reports success.

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/helpers/idempotent-promise-handler.ts (L15-36)
```typescript
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
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/helpers/__tests__/idempotent-promise-handler.test.ts (L76-103)
```typescript
  it('clears stale identifier from hash even when promise fails', async () => {
    // GIVEN
    const promiseFunctionErr = () => {
      throw new ShopifyError();
    };
    const currentDate = Date.now();
    jest.useFakeTimers().setSystemTime(currentDate);
    const promiseHandler = new IdempotentPromiseHandler() as any;

    // WHEN
    expect(
      promiseHandler.handlePromise({
        promiseFunction: promiseFunctionErr,
        identifier: 'old-promise',
      }),
    ).rejects.toThrow();

    jest.useFakeTimers().setSystemTime(currentDate + 70000);

    expect(
      promiseHandler.handlePromise({
        promiseFunction: promiseFunctionErr,
        identifier: 'new-promise',
      }),
    ).rejects.toThrow();

    expect(promiseHandler.identifiers.size).toBe(1);
  });
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts (L174-187)
```typescript
  private async handleAfterAuthHook(
    params: BasicParams,
    session: Session,
    request: Request,
    sessionToken: string,
  ) {
    const {config} = params;
    await config.idempotentPromiseHandler.handlePromise({
      promiseFunction: () => {
        return triggerAfterAuthHook(params, session, request, this);
      },
      identifier: sessionToken,
    });
  }
```

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L91-132)
```typescript
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
```

**File:** packages/apps/shopify-app-express/docs/reference/guides/token-exchange.md (L44-46)
```markdown
## Handling revoked access tokens

Token exchange reuses a stored access token while it is unexpired. If a token is **revoked** by the merchant before it expires, it still looks valid locally, so the library will use it and the Admin API call will fail with a `401`. The library does not automatically re-authenticate in that case, so your app should handle a `401` from the Admin API by re-running the request (which triggers a fresh token exchange) or re-authenticating. Expired tokens are handled automatically.
```
