### No Vulnerability found for this question.

**Rationale:**

The two claimed issues do not hold up under review of `performTokenExchange` in [1](#0-0) :

1. **Malformed `dest` crash claim is false.** The `new URL(payload.dest).hostname` call at [2](#0-1)  sits inside the outer `try` block that spans the entire function body, and any thrown error (including a `TypeError` from an invalid URL) is caught by the final `catch (error)` block at line 133, which falls through to the generic handler and returns a controlled `500 Internal Server Error` response rather than crashing the process: [3](#0-2) . This is exactly the "fail closed with a controlled error" behavior the prompt asks to verify — it already exists, per-request, with no unhandled exception or crash loop.

2. **No cross-tenant session overwrite is possible.** The `shop` value used to compute `sessionId` is derived from `payload.dest`, which comes from `api.session.decodeSessionToken(sessionToken)` — a JWT that must be validated (signature/audience/expiry) against the app's credentials before its claims are trusted [4](#0-3) . An attacker without the app secret cannot forge a token naming a different shop, so a replayed token can only ever reference the attacker's *own* shop. There is no cross-tenant clobbering: at most a user could race their own shop's session record, which does not constitute a security boundary violation since they already have legitimate access to that shop's session.

3. **Stale/expired token replay doesn't succeed as described.** `exchangeToken` forwards the session token to `api.auth.tokenExchange` at [5](#0-4) , which calls Shopify's OAuth token endpoint. An expired/invalid JWT is rejected server-side by Shopify, surfacing as an `HttpResponseError` with `invalid_subject_token` (400) or code 401, both of which are explicitly handled to invalidate the stale session and respond with a controlled error via `respondToInvalidSessionToken`/`invalidateAccessToken` rather than proceeding to `storeSession` [6](#0-5) . So the premise that a stale token can drive a successful `storeSession` overwrite does not hold.

Since there is no cross-tenant impact, no unhandled crash, and the described replay path is already blocked by existing JWT/OAuth validation and error handling, this does not meet the bar for a valid finding.

### Citations

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L24-38)
```typescript
async function exchangeToken(
  api: Shopify,
  config: AppConfigInterface,
  sessionToken: string,
  shop: string,
  requestedTokenType: RequestedTokenType,
): Promise<Session> {
  const {session} = await api.auth.tokenExchange({
    sessionToken,
    shop,
    requestedTokenType,
    expiring: config.future?.expiringOfflineAccessTokens,
  });
  return session;
}
```

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L53-166)
```typescript
export async function performTokenExchange({
  req,
  res,
  next,
  api,
  config,
  sessionToken,
}: PerformTokenExchangeParams): Promise<void> {
  const logger = config.logger;
  // Hoisted so the outer catch can invalidate a stale access token if needed.
  let sessionToInvalidate: Session | undefined;

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
      error instanceof InvalidJwtError ||
      (error instanceof HttpResponseError &&
        error.response.code === 400 &&
        error.response.body?.error === 'invalid_subject_token')
    ) {
      respondToInvalidSessionToken({
        api,
        req,
        res,
        message: error.message,
        retryRequest: true,
      });
      return;
    }

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

    logger.error(`Unexpected error during token exchange: ${error}`);
    res.status(500).send('Internal Server Error');
  }
}
```
