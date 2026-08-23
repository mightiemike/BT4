### Title
Unhandled `TypeError` from malformed `host` param crashes `sanitizeHost` outside the `throwOnInvalid` contract, causing uncontrolled exceptions in auth entrypoints - ([File: packages/apps/shopify-api/lib/utils/shop-validator.ts])

### Summary
`sanitizeHost` is documented and typed to only throw when explicitly asked to (`throwOnInvalid = true`), returning `null` otherwise for invalid input. In practice it can throw an unconditional, uncaught `TypeError` regardless of the `throwOnInvalid` flag whenever the base64-decoded `host` value is not a syntactically valid URL authority, because the `new URL()` call executes before the `throwOnInvalid` check.

### Finding Description
`sanitizeHost` first validates that the input matches a base64 character-set regex, then unconditionally calls `new URL('https://' + decodeHost(host))` to extract the hostname: [1](#0-0) 

`decodeHost` is a thin wrapper around `atob`: [2](#0-1) 

Passing base64-charset-valid input that decodes to a string containing a WHATWG-forbidden host code point (space, `\t`, `\n`, `\r`, NUL, `#`, `/`, `:`, `?`, `@`, `[`, `\`, `]`, `^`, `|`) makes `new URL(...)` throw a `TypeError: Invalid URL`. This throw happens *before* the `throwOnInvalid` gate at the bottom of the function is ever reached, so it fires even when callers pass `throwOnInvalid = false` (the default) expecting a graceful `null` return.

This function is invoked directly on unauthenticated, attacker-controlled request data at multiple auth entrypoints reachable by an anonymous browser/document request:
- `validateShopAndHostParams` in both `shopify-app-remix` and `shopify-app-react-router`, called unconditionally at the top of `authenticateAdmin` for any document (non-session-token) request: [3](#0-2) [4](#0-3) 
- `redirectToShopifyOrAppRoot` and `redirectWithExitIframe` helpers, which call `sanitizeHost(...)!` directly on the `host` query parameter. [5](#0-4) 
- `shopify-app-express`'s `redirectToShopifyOrAppRoot` and `redirectToAuth`. [6](#0-5) 

Crucially, `authenticateAdmin`'s top-level try/catch only special-cases errors that are already `Response` instances (adding CORS headers); any other thrown error/exception (like this raw `TypeError`) is simply re-thrown as-is: [7](#0-6) 

So instead of the intended behavior (treat invalid host as "no host", redirect to login/App Bridge), an anonymous request with a crafted `host` value produces an uncaught exception that bypasses the library's structured error/response handling.

### Impact Explanation
Any anonymous visitor can send a GET request to an app's embedded-app document route with `?shop=<valid>&host=<malicious-base64>` and trigger an unhandled exception inside the authentication entrypoint, before any session or credential check occurs. This is a DoS vector against the app's primary auth handler: instead of a controlled 401/302 response, the request handler throws a raw `TypeError` that propagates past the library's own error handling into the host framework's generic error boundary, on every request bearing the malformed host value. Depending on deployment (e.g., serverless handlers without their own catch-all, or workers), this can also leak stack traces or destabilize concurrent request handling.

### Likelihood Explanation
High reachability: the `host` query parameter is fully attacker-controlled, requires no authentication, and is checked on essentially every embedded-app document load. The base64 character-set pre-check does not guarantee the decoded value is a valid URL authority, so triggering the crash only requires basic knowledge of base64 encoding — no secrets or special access needed.

### Recommendation
Move the `new URL()` parsing inside a `try { ... } catch { sanitizedHost = null; }` block in `sanitizeHost` before evaluating `throwOnInvalid`, so malformed decoded hosts are treated the same as regex-mismatched hosts (return `null`, or throw `InvalidHostError` only when `throwOnInvalid` is explicitly `true`). Add regression tests covering base64-valid inputs that decode to strings with forbidden URL host code points (space, NUL, `#`, `@`, `\`, etc.) for both `throwOnInvalid: true` and `false`.

### Proof of Concept
```ts
import {shopifyApi} from '@shopify/shopify-api';

const shopify = shopifyApi(/* ...config */);

// "a b" -> valid base64 charset, decodes to a string with a forbidden URL host code point (space)
const maliciousHost = Buffer.from('a b').toString('base64'); // "YSBi"

// Throws uncaught TypeError: Invalid URL, even though throwOnInvalid defaults to false
shopify.utils.sanitizeHost(maliciousHost);
```
Sending `GET /?shop=test-shop.myshopify.com&host=YSBi` to any `shopify-app-remix`/`shopify-app-react-router` embedded app route reproduces the same unhandled exception inside `authenticateAdmin`.

### Citations

**File:** packages/apps/shopify-api/lib/utils/shop-validator.ts (L52-60)
```typescript
export function sanitizeHost(config: ConfigInterface) {
  return (host: string, throwOnInvalid = false): string | null => {
    const base64regex = /^[0-9a-zA-Z+/]+={0,2}$/;

    let sanitizedHost = base64regex.test(host) ? host : null;
    if (sanitizedHost) {
      const {hostname} = new URL(`https://${decodeHost(sanitizedHost)}`);

      const originsRegex = [
```

**File:** packages/apps/shopify-api/lib/auth/decode-host.ts (L1-3)
```typescript
export function decodeHost(host: string): string {
  return atob(host);
}
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts (L11-22)
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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/redirect-to-shopify-or-app-root.ts (L11-14)
```typescript
  const url = new URL(request.url);

  const host = api.utils.sanitizeHost(url.searchParams.get('host')!)!;
  const shop = api.utils.sanitizeShop(url.searchParams.get('shop')!)!;
```

**File:** packages/apps/shopify-app-express/src/middlewares/redirect-to-shopify-or-app-root.ts (L22-22)
```typescript
      const host = api.utils.sanitizeHost(req.query.host as string)!;
```
