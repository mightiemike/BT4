### Title
Unbounded, uncancellable wait on the `afterAuth` hook inside `IdempotentPromiseHandler` can indefinitely stall the token‑exchange auth path - (File: `packages/apps/shopify-app-express/src/helpers/idempotent-promise-handler.ts`)

### Summary
The external report describes a class of bug where an outbound verification call has no timeout/cancellation mechanism, so a slow or unresponsive external endpoint leaves the system in a permanently "pending" state that blocks legitimate follow-up requests. The `IdempotentPromiseHandler` used by the token-exchange authentication path in `shopify-app-express`, `shopify-app-remix`, and `shopify-app-react-router` has the same structural weakness: it marks a request "in flight" before awaiting the wrapped promise, awaits it with no timeout, and only clears the marker in a `finally` block that never runs if the promise never settles.

### Finding Description
`IdempotentPromiseHandler.handlePromise` first calls `isPromiseRunnable(identifier)`, which immediately records the identifier in the `identifiers` map, and only afterwards `await`s `promiseFunction()`: [1](#0-0) 

There is no timeout, `AbortSignal`, or cancellation path attached to `promiseFunction()`. If the function passed in never settles (e.g. because the developer's `hooks.afterAuth` callback makes an outbound call — such as `registerWebhooks` — to an endpoint that never responds), the `await` in `handlePromise` hangs forever, and `clearStaleIdentifiers()` in the `finally` block never executes, so the identifier is never removed from the map.

This handler is invoked from the token-exchange authentication strategies on every request that needs a fresh access token: [2](#0-1) [3](#0-2) [4](#0-3) 

`callAfterAuthHook`/`handleAfterAuthHook` is `await`ed directly in the authentication flow before the request handler proceeds to `next()` or returns the session, e.g.: [5](#0-4) 

So exactly like the external report's Chainlink GET request that can be left indefinitely pending with no cancel/retry provision, an unresponsive external call inside `afterAuth` leaves: (1) the current request hanging with no way for the app to recover, and (2) the identifier permanently marked as "already running" in the `Map`, so any other request sharing the same identifier (the raw session token string) will silently skip calling `promiseFunction` at all and fall through to `next()`/return the session as if the hook had completed successfully — even though it never did.

### Impact Explanation
This is a denial-of-service on the authentication handler path: a single slow/unresponsive external call inside `afterAuth` can hang the request indefinitely with no built-in timeout or cancellation, and can cause other in-flight requests using the same session token to proceed as though post-auth setup (e.g., webhook registration) succeeded when it did not. Because this code lives in the shared `shopify-app-express`/`shopify-app-remix`/`shopify-app-react-router` libraries, this is a library-level structural gap rather than isolated to one app's hook implementation.

### Likelihood Explanation
This requires an app to configure a `hooks.afterAuth` callback that makes an external network call capable of hanging (a common and recommended pattern, e.g. `registerWebhooks`), and for that external dependency to become slow/unresponsive — a realistic operational condition, not an adversarial one, mirroring the "external adapter timeout" trigger in the original report.

### Recommendation
Wrap the `promiseFunction()` invocation in `IdempotentPromiseHandler.handlePromise` with an explicit timeout (e.g., `Promise.race` against a timer or an `AbortSignal`), and always clear the identifier (in `finally`, or via a dedicated cleanup on timeout) regardless of whether the promise settles, so a hung external call cannot indefinitely block the identifier or the caller.

### Proof of Concept
1. Configure an app with `hooks.afterAuth` that calls an external endpoint which never responds (simulating an external-adapter timeout, as in the original report).
2. Trigger the token-exchange path (`performTokenExchange` / `token-exchange.ts` strategy) with a session token; `handlePromise` marks the identifier and awaits `afterAuth` indefinitely. [6](#0-5) 
3. Issue a second request/retry with the same session token before the first one ever resolves; observe that `isPromiseRunnable` returns `false`, the hook is skipped entirely, and the flow proceeds to `next()`/returns the session as though `afterAuth` succeeded — with the first request left hanging forever.

### Citations

**File:** packages/apps/shopify-app-express/src/helpers/idempotent-promise-handler.ts (L15-28)
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
```

**File:** packages/apps/shopify-app-express/src/helpers/idempotent-promise-handler.ts (L30-44)
```typescript
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
```

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

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L118-132)
```typescript
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

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/admin/strategies/token-exchange.ts (L76-90)
```typescript
  async function handleAfterAuthHook(
    session: Session,
    request: Request,
    sessionToken: string,
  ) {
    await config.idempotentPromiseHandler.handlePromise({
      promiseFunction: () => {
        return triggerAfterAuthHook(params, session, request, {
          authenticate,
          handleClientError,
        });
      },
      identifier: sessionToken,
    });
  }
```
