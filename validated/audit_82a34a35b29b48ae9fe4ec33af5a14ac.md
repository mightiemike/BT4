### Title
Non-atomic idempotency marking in `IdempotentPromiseHandler` permanently skips `afterAuth` hook execution after a failure, silently completing OAuth/token-exchange for the client - (File: `packages/apps/shopify-app-express/src/helpers/idempotent-promise-handler.ts`, also duplicated in `packages/apps/shopify-app-remix/src/server/authenticate/helpers/idempotent-promise-handler.ts` and `packages/apps/shopify-app-react-router/src/server/authenticate/helpers/idempotent-promise-handler.ts`)

### Summary
This is the closest structural analog to the reported bug class ("an operation is marked as processed before it is durably completed, and there is no way to un-process it, so subsequent legitimate attempts are silently blocked"). In `IdempotentPromiseHandler`, an identifier is marked as "used" (`isPromiseRunnable`) *before* the guarded promise (the app's `afterAuth` hook) actually finishes, and if that promise throws, the identifier is **not** cleared — it simply expires after a fixed 60-second TTL. Any retry of the token-exchange flow using the same session token during that window silently no-ops the `afterAuth` hook while still returning a 200/success response to the client.

### Finding Description
`IdempotentPromiseHandler.handlePromise` calls `isPromiseRunnable(identifier)`, which immediately inserts the identifier into the map and returns `true` on first sight [1](#0-0) . The actual `promiseFunction()` execution happens afterward, and if it throws, the `catch`/`finally` block only prunes entries older than `IDENTIFIER_TTL_MS` (60 seconds) — it does **not** remove the identifier that just failed [2](#0-1) . The identifier stays "marked done" for up to 60 seconds after the failure.

This handler is used to guard the `afterAuth` hook call in the token-exchange flow, keyed by the raw `sessionToken` JWT: in `shopify-app-express`'s `performTokenExchange`, `callAfterAuthHook` invokes `handlePromise({promiseFunction: () => config.hooks?.afterAuth?.({session}), identifier: sessionToken})` [3](#0-2) , and if that call throws, the outer handler in `performTokenExchange` returns a 500 to the client [4](#0-3) . The same pattern exists in `shopify-app-remix`'s `TokenExchangeStrategy.handleAfterAuthHook` [5](#0-4)  and in `shopify-app-react-router`'s equivalent strategy [6](#0-5) .

If the merchant/browser (App Bridge) retries the same request with the same session token (App Bridge session tokens are short-lived and frequently reused for retries within seconds), `isPromiseRunnable` returns `false` on the retry, so `promiseFunction` (the `afterAuth` hook — which typically performs mandatory setup such as webhook registration, billing checks, or app-scoped provisioning) is skipped entirely, yet `performTokenExchange`/`authenticate` still proceeds to `next()`/returns the session as if authentication and setup succeeded [7](#0-6) . This mirrors the reported bug class: a state transition ("processed") is committed optimistically, the completing step can fail, and there's no mechanism to "unprocess" the identifier — legitimate follow-up attempts are silently no-op'd instead of retried, until an opaque 60-second timer expires.

### Impact Explanation
Impact is Medium: for up to 60 seconds after a first `afterAuth` failure, retried token-exchange requests using the same session token will bypass the `afterAuth` hook without any error surfaced to the app or merchant, since the outer flow otherwise treats the request as authenticated. Depending on what the app's `afterAuth` hook does (e.g., register mandatory webhooks, persist billing state), this can leave the merchant's session/installation in an inconsistent state that looks successful but is functionally incomplete — analogous to funds/state being "frozen" until an internal timer expires, with no operator-facing way to reset it.

### Likelihood Explanation
Likelihood is Medium: it requires (a) a transient failure in the app's own `afterAuth` hook (network blip, DB error, etc.) and (b) a client-side retry with the identical session token within the 60-second TTL window — both plausible under normal operating conditions since App Bridge/browsers commonly retry failed auth requests using the same still-valid JWT.

### Recommendation
Only mark an identifier as "consumed" after `promiseFunction()` resolves successfully; on failure, remove the identifier immediately (rather than waiting for the TTL sweep) so a retry can re-attempt the `afterAuth` hook. Consider making `isPromiseRunnable`/completion tracking atomic (e.g., store a pending/failed/succeeded state) instead of a single insert-on-first-sight flag, and only skip re-execution for identifiers that completed successfully.

### Proof of Concept
1. Configure an app with `afterAuth` hook that occasionally throws (e.g., a transient DB write failure).
2. Client obtains a session token and calls the token-exchange endpoint; `afterAuth` throws, `performTokenExchange` returns 500, but `sessionToken` is now recorded as "seen" in `IdempotentPromiseHandler.identifiers`.
3. Client retries the same request within 60 seconds using the identical (still valid) session token.
4. `isPromiseRunnable(sessionToken)` returns `false` (already present in map), so `afterAuth` is silently skipped; `performTokenExchange` proceeds to `next()`/returns the session as a success, even though the setup work in `afterAuth` never completed on this retry.

### Citations

**File:** packages/apps/shopify-app-express/src/helpers/idempotent-promise-handler.ts (L15-44)
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

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L123-132)
```typescript
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
