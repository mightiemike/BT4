### Title
`afterAuth` hook (webhook registration) can be permanently skipped after a transient failure during token exchange - ([File: packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts])

### Summary
The token-exchange authentication strategy performs a multi-step chain (offline token exchange → store session → optional online token exchange → store session → `afterAuth` hook) but persists the offline session to storage *before* the terminal step (`afterAuth` hook, typically used to register webhooks) completes. If that terminal step fails for any transient reason, the previously-stored session is already "active," so on the very next request the strategy takes an early-return path that bypasses the whole exchange chain — including the `afterAuth` hook — leaving the merchant's webhook registration (or whatever the app wired into `afterAuth`) permanently un-run until the access token nears expiry again.

### Finding Description
In `authenticate()`, the strategy only re-runs the exchange chain (and therefore the `afterAuth` hook) when there is no session or the session is not active: [1](#0-0) 

The offline session is written to `sessionStorage` immediately after the offline exchange succeeds, well before the `afterAuth` hook runs: [2](#0-1) 

The `afterAuth` hook is only invoked afterward, wrapped in a try/catch that converts any failure into a `500 Response`: [3](#0-2) 

Because the offline session was already persisted as `isActive()` (valid, unexpired), any subsequent request for that shop takes the early-return branch at the top of `authenticate()` and returns `session!` directly, never re-entering the block that calls `handleAfterAuthHook`/`triggerAfterAuthHook`. The identical pattern exists in the react-router package (`packages/apps/shopify-app-react-router/src/server/authenticate/admin/strategies/token-exchange.ts`) and in the Express middleware, where the same early-active-session short circuit appears before the exchange+hook block: [4](#0-3) 

This mirrors the reported bug class: a beneficial, one-time side effect (here, running `afterAuth` — per the docs typically used to call `shopify.registerWebhooks({session})` — analogous to sending out "rewards") is gated on the completion of the *last* sub-step of a multi-step operation chain. If that last step fails after earlier steps already committed state (the stored, active offline session), the side effect is silently and durably skipped, and the normal request path offers no future trigger to retry it, since the "already active session" fast path is exactly what suppresses re-entry into the hook-calling code.

### Impact Explanation
An app that relies on `afterAuth` to register webhooks (as explicitly recommended in the docs: `hooks: { afterAuth: async ({session}) => { await shopify.registerWebhooks({session}); } }`) can end up with a merchant install that never receives webhook registrations, because any transient error in the hook itself, or in the subsequent online-token exchange when `useOnlineTokens` is enabled, prevents `afterAuth` from firing while the stored offline session remains valid and bypasses all future retries. This is a functional/availability defect in a security-relevant auth handler (loss of webhook delivery can itself have security implications, e.g., missed `app/uninstalled` or GDPR webhooks), reachable purely through normal, unprivileged embedded-app traffic with no attacker action required — it only needs an external hiccup (network blip, transient 5xx from the `afterAuth`-triggered mutation, etc.) at the wrong moment.

### Likelihood Explanation
This requires only a single transient failure during the tail of the token-exchange flow (e.g., a temporary error inside the app's `afterAuth` hook, or in the online-token exchange call when `useOnlineTokens` is on) — no adversarial input, timing race, or privileged access is required. Given normal production error rates for network calls, this is a plausible, externally-triggerable condition, matching the "reliant on external conditions" acceptance criteria used to validate the original finding.

### Recommendation
Do not treat a stored session as sufficient to skip re-running `afterAuth`/webhook registration; instead, track completion of the full chain (e.g., persist a flag or idempotency marker only after `afterAuth` succeeds, or make `afterAuth`/webhook registration idempotent and always re-attempted for sessions that haven't confirmed successful hook completion), so a later request can safely retry the terminal step even though the token itself is still valid.

### Proof of Concept
1. Configure an embedded app with `future.tokenExchange` and an `afterAuth` hook that calls `shopify.registerWebhooks({session})`.
2. Have the first authenticated request go through the exchange chain in `authenticate()`; the offline session is exchanged and stored via `config.sessionStorage!.storeSession(offlineSession)`.
3. Cause the `afterAuth` hook (or, with `useOnlineTokens` enabled, the online-token exchange call) to throw once — e.g., a transient 5xx from the GraphQL webhook-registration mutation.
4. The request returns `500`, but the offline session remains stored as active.
5. On the next request for the same shop, `authenticate()` finds `session.isActive(...) === true` and returns the session immediately without ever invoking `afterAuth` again — webhook registration is never retried for that install.

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts (L54-82)
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
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts (L89-107)
```typescript
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
```

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L84-132)
```typescript
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
```
