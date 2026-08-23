### Title
`sanitizeHost()` throws an uncaught `DOMException` on malformed base64 input instead of returning `null` - ([File: packages/apps/shopify-api/lib/utils/shop-validator.ts])

### Summary
`sanitizeHost(config)` validates the `host` param with a regex that does not enforce valid base64 length/padding before calling `decodeHost` (which wraps `atob`). Certain strings pass the regex but are rejected by the WHATWG "forgiving-base64" decode algorithm used by `atob`, causing an uncaught `DOMException`/`InvalidCharacterError` instead of the expected `null` return value.

### Finding Description
`sanitizeHost` validates its input with `base64regex = /^[0-9a-zA-Z+/]+={0,2}$/` and, if it matches, immediately calls `decodeHost(sanitizedHost)`, which is a thin wrapper around `atob()`. [1](#0-0) [2](#0-1) 

The regex only checks the character set and 0-2 trailing `=` signs; it does not verify that the base64 payload length is valid (i.e., that the non-padding portion's length mod 4 isn't 1). Per the WHATWG forgiving-base64 decode algorithm implemented by Node's global `atob`, any input whose length (after any padding removal) is `≡ 1 (mod 4)` — e.g., a single character like `"A"` or `"A="` — is invalid and causes `atob` to throw a `DOMException` (`InvalidCharacterError`), rather than returning a decode failure that the caller can handle gracefully.

This function is reachable directly from `validateShopAndHostParams` in the Remix/React Router `authenticate.admin()` flow for any embedded app, using the unauthenticated `host` query parameter: [3](#0-2) 

The call happens before any session-token/HMAC validation, so an attacker needs no credentials — a bare `GET /?shop=x.myshopify.com&host=A` to any embedded route is sufficient. The `throwOnInvalid` guard inside `sanitizeHost` (which would raise a controlled `InvalidHostError`) is irrelevant here because the uncaught exception originates from `decodeHost`/`atob` *before* that check is ever reached, so the function's documented contract ("return `null` for invalid host") is broken regardless of the `throwOnInvalid` flag. [4](#0-3) 

### Impact Explanation
The exception propagates out of `validateShopAndHostParams` into `authenticateAdmin`'s `try/catch`, which merely re-throws it: [5](#0-4) 

Because each HTTP request in Remix/React Router (and similarly in Express) is handled independently, this results in an uncaught error surfaced as a 500 response for that single request rather than a full process/worker crash affecting other concurrent or subsequent requests, as the question's "Advanced DoS" premise (crashing the worker for every subsequent request) claims. The concrete, verifiable impact is limited to: a raw, unhandled `DOMException` being thrown for the requesting client's own request instead of the intended graceful redirect-to-login behavior — a minor availability/robustness defect in the auth handler for a single request, not a sustained denial-of-service against the app or other users.

### Likelihood Explanation
Trivial to trigger: any unauthenticated client can send a single crafted `host` query parameter value (e.g. `"A"`) to an embedded-app route guarded by `authenticate.admin()`, requiring only `config.isEmbeddedApp = true` (the default for embedded apps), no other non-default configuration or secrets.

### Recommendation
Wrap `decodeHost`'s `atob` call in `sanitizeHost` in a try/catch, treating any decode failure the same as a regex mismatch (return `null` / throw `InvalidHostError` per `throwOnInvalid`), rather than letting the raw `DOMException` propagate.

### Proof of Concept
```ts
import {shopifyApi} from '@shopify/shopify-api';
import {testConfig} from '@shopify/shopify-api/lib/__tests__/test-config';

test('sanitizeHost throws uncaught DOMException for malformed base64', () => {
  const shopify = shopifyApi(testConfig());
  // "A" passes base64regex (/^[0-9a-zA-Z+/]+={0,2}$/) but has invalid
  // base64 length (1 mod 4), causing atob() to throw instead of the
  // function returning null.
  expect(() => shopify.utils.sanitizeHost('A')).toThrow(); // throws DOMException, not InvalidHostError, not returning null
});
```
Request-level PoC: `GET /?shop=x.myshopify.com&host=A` to any route wrapped by `authenticate.admin()` in an embedded Remix/React Router app returns an unhandled 500 error instead of a redirect to the login path.

### Citations

**File:** packages/apps/shopify-api/lib/utils/shop-validator.ts (L52-58)
```typescript
export function sanitizeHost(config: ConfigInterface) {
  return (host: string, throwOnInvalid = false): string | null => {
    const base64regex = /^[0-9a-zA-Z+/]+={0,2}$/;

    let sanitizedHost = base64regex.test(host) ? host : null;
    if (sanitizedHost) {
      const {hostname} = new URL(`https://${decodeHost(sanitizedHost)}`);
```

**File:** packages/apps/shopify-api/lib/utils/shop-validator.ts (L85-90)
```typescript
    if (!sanitizedHost && throwOnInvalid) {
      throw new InvalidHostError('Received invalid host argument');
    }

    return sanitizedHost;
  };
```

**File:** packages/apps/shopify-api/lib/auth/decode-host.ts (L1-3)
```typescript
export function decodeHost(host: string): string {
  return atob(host);
}
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts (L11-29)
```typescript
  if (config.isEmbeddedApp) {
    const url = new URL(request.url);
    const shop = api.utils.sanitizeShop(url.searchParams.get('shop')!);
    if (!shop) {
      logger.debug('Missing or invalid shop, redirecting to login path', {
        shop,
      });
      throw redirectToLoginPath(request, params);
    }

    const host = api.utils.sanitizeHost(url.searchParams.get('host')!);
    if (!host) {
      logger.debug('Invalid host, redirecting to login path', {
        shop,
        host: url.searchParams.get('host'),
      });
      throw redirectToLoginPath(request, params);
    }
  }
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts (L143-186)
```typescript
  return async function authenticateAdmin(request: Request) {
    try {
      respondToBotRequest(params, request);
      respondToOptionsRequest(params, request);
      await respondToBouncePageRequest(request);
      await respondToExitIframeRequest(request);
      await strategy.respondToOAuthRequests(request);

      // If this is a valid request, but it doesn't have a session token header, this is a document request. We need to
      // ensure we're embedded if needed and we have the information needed to load the session.
      if (!getSessionTokenHeader(request)) {
        validateShopAndHostParams(params, request);
        await ensureAppIsEmbeddedIfRequired(params, request);
        await ensureSessionTokenSearchParamIfRequired(params, request);
      }

      const {payload, shop, sessionId, sessionToken} =
        await getSessionTokenContext(params, request);

      logger.info('Authenticating admin request', {shop});

      logger.debug('Loading session from storage', {shop, sessionId});
      const existingSession = sessionId
        ? await config.sessionStorage!.loadSession(sessionId)
        : undefined;

      const session = await strategy.authenticate(request, {
        session: existingSession,
        sessionToken,
        shop,
      });

      return createContext(request, session, strategy, payload);
    } catch (errorOrResponse) {
      if (errorOrResponse instanceof Response) {
        logger.debug('Authenticate returned a response', {
          shop: getShopFromRequest(request),
        });
        ensureCORSHeadersFactory(params, request)(errorOrResponse);
      }

      throw errorOrResponse;
    }
  };
```
