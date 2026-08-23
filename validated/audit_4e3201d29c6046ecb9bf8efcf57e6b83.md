### Title
Session storage/lookup errors leak raw internal error messages to unauthenticated HTTP clients - (File: `packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts`)

### Summary
The `shopify-app-express` `validateAuthenticatedSession` middleware, which runs on every protected app route to authenticate a request against Shopify's session/token model, returns raw `Error.message` content from internal session-lookup failures directly in the HTTP response body instead of a generic error message.

### Finding Description
In `validateWithAuthCodeFlow`, two failure paths call back into the response with the unmodified `.message` of an internal exception:

- When `api.session.getCurrentId(...)` throws, the error is passed to `handleSessionError`, whose default branch does `res.status(500); res.send(error.message);` [1](#0-0) .
- When `config.sessionStorage.loadSession(sessionId)` throws (e.g. a database/storage backend error), the handler does the same directly inline: `res.status(500); res.send(error.message); return undefined;` [2](#0-1) .

Both paths run before any successful authentication is established — they are reachable by any caller (anonymous or with a forged/expired bearer token) hitting a route wrapped by `validateAuthenticatedSession()`, which is the primary request-authentication handler used by Express-based Shopify apps [3](#0-2) . The existing test suite confirms this behavior is intentional/expected today: a mocked session-storage rejection with message `'Storage error'` is echoed verbatim in the HTTP response body [4](#0-3) .

This is directly analogous to the reported bug class: an application-level error handler exposing implementation details (in the report's case, filesystem paths/stack trace from `body-parser`; here, whatever internal exception message the configured `SessionStorage` implementation throws — which for common storage adapters can include connection strings, hostnames, driver/library names, query fragments, or file paths) to an unauthenticated network caller.

### Impact Explanation
Any real-world `SessionStorage` implementation (SQL, Redis, MongoDB, custom adapters) can throw errors whose `.message` embeds sensitive backend details — e.g., database hostnames/ports, credentials embedded in connection error text, driver/version fingerprints, or internal file paths — that get surfaced verbatim to an anonymous or improperly authenticated caller. This is an information-disclosure vulnerability that eases follow-on attacks (backend fingerprinting, targeted exploitation of the storage backend) even though it does not by itself grant an access token or bypass authentication.

### Likelihood Explanation
Triggering this requires only causing the configured session storage backend to throw during `loadSession`/`getCurrentId` — e.g., transient DB/connection issues, malformed session IDs, or storage backend misconfiguration/outage — while sending any request (with or without a valid-looking bearer token) to a protected route. No privileged access or MITM is required; this is a normal error path reachable from a single anonymous/merchant request.

### Recommendation
In `handleSessionError` and the `loadSession` catch block, stop sending `error.message` to the client. Log the full error server-side (already done via `config.logger.error`) and return a generic message such as `'Internal Server Error'` in the response body for the default/500 branches, consistent with the safer pattern already used elsewhere in the codebase (e.g. `packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts`, which sends `'Internal Server Error'` rather than the raw error) [5](#0-4) .

### Proof of Concept
1. Configure an Express app using `shopifyApp(...)` with `validateAuthenticatedSession()` protecting a route.
2. Cause `config.sessionStorage.loadSession` (or `api.session.getCurrentId`) to throw, e.g. by pointing the session storage at an unreachable/misconfigured database, or as reproduced in the existing test by mocking a rejection.
3. Send `GET /test/shop?shop=my-shop.myshopify.io` with a validly-signed (or even missing) bearer token.
4. Observe the HTTP 500 response body contains the raw `error.message` from the storage layer, as demonstrated by the existing test expecting `response.error.text === 'Storage error'` [4](#0-3) .

### Citations

**File:** packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts (L72-91)
```typescript
async function validateWithAuthCodeFlow({
  req,
  res,
  next,
  api,
  config,
}: StrategyParams): Promise<unknown> {
  let sessionId: string | undefined;
  try {
    sessionId = await api.session.getCurrentId({
      isOnline: config.useOnlineTokens,
      rawRequest: req,
      rawResponse: res,
    });
  } catch (error) {
    config.logger.error(`Error when loading session from storage: ${error}`);

    handleSessionError(req, res, error);
    return undefined;
  }
```

**File:** packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts (L93-103)
```typescript
  let session: Session | undefined;
  if (sessionId) {
    try {
      session = await config.sessionStorage.loadSession(sessionId);
    } catch (error) {
      config.logger.error(`Error when loading session from storage: ${error}`);

      res.status(500);
      res.send(error.message);
      return undefined;
    }
```

**File:** packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts (L185-196)
```typescript
function handleSessionError(_req: Request, res: Response, error: Error) {
  switch (true) {
    case error instanceof InvalidJwtError:
      res.status(401);
      res.send(error.message);
      break;
    default:
      res.status(500);
      res.send(error.message);
      break;
  }
}
```

**File:** packages/apps/shopify-app-express/src/middlewares/__tests__/validate-authenticated-session.test.ts (L217-228)
```typescript
    it('returns a 500 if the storage throws an error', async () => {
      jest
        .spyOn(shopify.config.sessionStorage, 'loadSession')
        .mockRejectedValueOnce(new Error('Storage error'));

      const response = await request(app)
        .get('/test/shop?shop=my-shop.myshopify.io')
        .set({Authorization: `Bearer ${validJWT}`})
        .expect(500);

      expect((response.error as any).text).toBe('Storage error');
    });
```

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L76-82)
```typescript
      session = await config.sessionStorage.loadSession(sessionId);
      sessionToInvalidate = session;
    } catch (error) {
      logger.error(`Error when loading session from storage: ${error}`);
      res.status(500).send('Internal Server Error');
      return;
    }
```
