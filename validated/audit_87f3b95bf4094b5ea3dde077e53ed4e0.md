### Title
Malformed base64 `host` parameter crashes `sanitizeHost()`, causing unhandled request hangs in OAuth redirect handlers - (File: `packages/apps/shopify-api/lib/utils/shop-validator.ts`)

### Summary
`sanitizeHost()` validates the `host` query parameter's *character set* with a base64 regex but does not validate that its length is actually decodable, nor does it (or any of its Express-app callers) wrap the `atob()`/`new URL()` calls in a try/catch. A crafted `host` value that passes the regex but has an invalid base64 length (length mod 4 === 1) causes `atob()` to throw, and that exception propagates uncaught through `clientSideRedirect()` and the async Express route handlers, leaving the HTTP request hanging with no response. The open-redirect portion of the original hypothesis does not hold: the redirect destination is always built from the app's own configured `hostName`, never from the attacker-supplied `host`.

### Finding Description
`sanitizeHost()` only checks that every character of `host` belongs to the base64 alphabet: [1](#0-0) 
It never validates that the string length is a multiple of 4 (minus optional padding). `decodeHost()` calls the global `atob()` directly with no error handling: [2](#0-1) 
Per the WHATWG forgiving-base64 algorithm implemented by Node's `atob`, a string whose length (mod 4) is 1 is invalid and `atob()` throws a `DOMException`. A string such as `"abcde"` (5 base64-alphabet characters, no `=`) passes the `base64regex` check in `sanitizeHost` but throws inside `decodeHost()`, which is called unguarded inside `sanitizeHost()`.

This exception propagates to `clientSideRedirect()` in the Express package, which also has no try/catch around `api.utils.sanitizeHost(...)`: [3](#0-2) 
`redirectToAuth()` is invoked directly (not awaited/caught) from two places that also lack surrounding try/catch:
- `auth.begin()` route handler: `return redirectToAuth({req, res, api, config});` [4](#0-3) 
- `ensureInstalledOnShop` middleware: `return redirectToAuth({req, res, api, config});` (two call sites) [5](#0-4) 

Because these are async Express handlers, a synchronous throw inside them becomes a rejected promise. Express 4/5 route dispatch does not automatically forward promise rejections from handlers unless the app explicitly awaits and calls `next(err)`; here nothing does, so the rejection becomes an unhandled promise rejection and the HTTP response is never sent — the client's request hangs until a proxy/client-side timeout.

The claimed **open-redirect** portion of the hypothesis is not supported by the code: `clientSideRedirect()` builds `redirectUri` from `api.config.hostScheme`/`api.config.hostName` (the app's own configured host), not from the attacker-controlled `host` value — the sanitized `host` is only appended as a query-string value, never as the redirect authority: [6](#0-5) 
So even if `sanitizeHost`'s hostname check were bypassed, the destination stays same-origin as `config.hostName`. That part of the finding is invalid.

### Impact Explanation
This is a scoped Denial-of-Service in an authentication-adjacent handler (`/auth` begin route and the embedded-app installation-check middleware): an anonymous, unauthenticated attacker can send a single crafted GET request with `host=abcde&embedded=1` (or trigger the same path through `ensureInstalledOnShop`) causing the request to hang indefinitely with no response and an unhandled promise rejection logged server-side. Repeated requests can be used to exhaust available request-handling resources (open connections/timers) in apps built on `@shopify/shopify-app-express`.

### Likelihood Explanation
Fully reachable by an unprivileged attacker with default configuration: no secret, no prior session, no privileged role required — just a crafted query string on any public OAuth `/auth` or embedded-app entry route. It is deterministic and repeatable (any base64-alphabet string whose length mod 4 equals 1, e.g. `"abcde"`, or `"aaaaaaaaa"`).

### Recommendation
- In `sanitizeHost()` (`packages/apps/shopify-api/lib/utils/shop-validator.ts`), wrap the `decodeHost()`/`new URL()` calls in a try/catch and treat any decode/parse failure the same as an invalid host (return `null` or throw `InvalidHostError` per `throwOnInvalid`), rather than letting the exception propagate.
- Alternatively/additionally, harden the `base64regex` to require a length that is a multiple of 4 (accounting for padding), e.g. `^(?:[0-9a-zA-Z+/]{4})*(?:[0-9a-zA-Z+/]{2}==|[0-9a-zA-Z+/]{3}=|[0-9a-zA-Z+/]{4})$`.
- In `packages/apps/shopify-app-express/src/redirect-to-auth.ts` and its callers (`auth/index.ts`, `middlewares/ensure-installed-on-shop.ts`), wrap calls to `redirectToAuth`/`sanitizeHost` in try/catch and respond with a 4xx error instead of allowing an unhandled rejection.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/utils/__tests__/shop-validator.test.ts (new test)
import {shopifyApi} from '../..';
import {testConfig} from '../../__tests__/test-config';

test('sanitizeHost throws instead of returning null for malformed base64 length', () => {
  const shopify = shopifyApi(testConfig());

  // 5 chars of valid base64 alphabet, length % 4 === 1 -> atob() throws
  expect(() => shopify.utils.sanitizeHost('abcde')).toThrow();
});
```
Expected (buggy) behavior: the call throws a raw `DOMException`/`TypeError` instead of returning `null` gracefully or a controlled `InvalidHostError`.

Request-level PoC against an Express app built on `@shopify/shopify-app-express`:
```
GET /auth?shop=test-shop.myshopify.com&host=abcde&embedded=1 HTTP/1.1
Host: your-app.example.com
```
Expected (buggy) behavior: the server never sends an HTTP response (connection hangs until timeout) and logs an unhandled promise rejection, instead of returning a 4xx "Invalid host" response.

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

**File:** packages/apps/shopify-api/lib/auth/decode-host.ts (L1-3)
```typescript
export function decodeHost(host: string): string {
  return atob(host);
}
```

**File:** packages/apps/shopify-app-express/src/redirect-to-auth.ts (L30-42)
```typescript
function clientSideRedirect(
  api: Shopify,
  config: AppConfigInterface,
  req: Request,
  res: Response,
  shop: string,
): void {
  const host = api.utils.sanitizeHost(req.query.host as string);
  if (!host) {
    res.status(500);
    res.send('No host provided');
    return;
  }
```

**File:** packages/apps/shopify-app-express/src/redirect-to-auth.ts (L44-47)
```typescript
  const redirectUriParams = new URLSearchParams({shop, host}).toString();
  const redirectUri = `${api.config.hostScheme}://${api.config.hostName}${config.auth.path}?${redirectUriParams}`;

  redirectOutOfApp({config, api})({req, res, redirectUri, shop});
```

**File:** packages/apps/shopify-app-express/src/auth/index.ts (L14-29)
```typescript
    begin(): RequestHandler {
      return async (req: Request, res: Response) => {
        if (usesTokenExchange()) {
          config.logger.error(
            'auth.begin() was called while token exchange is enabled. Embedded apps using token exchange do not use the Auth Code flow routes.',
          );
          res
            .status(400)
            .send(
              'This app uses token exchange (tokenExchange). The OAuth auth routes are not used in this mode.',
            );
          return;
        }

        return redirectToAuth({req, res, api, config});
      };
```

**File:** packages/apps/shopify-app-express/src/middlewares/ensure-installed-on-shop.ts (L62-82)
```typescript
      if (!session && !req.originalUrl.match(exitIframeRE)) {
        config.logger.debug(
          'App installation was not found for shop, redirecting to auth',
          {shop},
        );

        return redirectToAuth({req, res, api, config});
      }

      if (api.config.isEmbeddedApp && req.query.embedded !== '1') {
        if (await sessionHasValidAccessToken(api, config, session)) {
          await embedAppIntoShopify(api, config, req, res, shop);
          return undefined;
        } else {
          config.logger.info(
            'Found a session, but it is not valid. Redirecting to auth',
            {shop},
          );

          return redirectToAuth({req, res, api, config});
        }
```
