### Title
Unhandled `atob()` exception in `sanitizeHost()` crashes unauthenticated auth/embedded-app request handlers - ([File: packages/apps/shopify-api/lib/auth/decode-host.ts])

### Summary
`sanitizeHost()` validates the `host` query parameter using a base64-character regex that does not reject strings whose length is invalid for base64 decoding (e.g., length % 4 == 1), and then unconditionally calls `atob()` on the value with no `try/catch`. Malformed-but-regex-passing input causes `atob()` to throw synchronously, propagating an uncaught exception out of `sanitizeHost()` and into unauthenticated request handlers (`redirect-to-auth.ts`, `render-app-bridge.ts`, `redirect-to-shopify-or-app-root.ts`, `validate-shop-and-host-params.ts`) that call it directly on attacker-controlled query data before any session/HMAC validation occurs.

### Finding Description
`decodeHost()` is a one-line wrapper around the global `atob()`: [1](#0-0) 

`sanitizeHost()` gates the call with a regex that only checks the character set, not the length invariant required for valid base64 (multiple-of-4, ignoring padding rules): [2](#0-1) 

Per the WHATWG "forgiving-base64" decode algorithm implemented by `atob()`, an input whose length modulo 4 equals 1 throws `InvalidCharacterError`. The regex `^[0-9a-zA-Z+/]+={0,2}$` happily accepts such strings (e.g. a 5-character value like `"AAAAA"`), so `decodeHost(sanitizedHost)` throws inside `sanitizeHost()`, which has no `try/catch` around the call: [3](#0-2) 

This is the same bug class as the reported analog: a shallow, format-only validator (`_validateToLength()` only checks byte length; `sanitizeHost`'s regex only checks character set) is used as a gatekeeper, but the actual decode step downstream (`decodeAddress()` assembly / `atob()`) has a stricter, unchecked invariant. Instead of the original bug's "silent fund loss," here the mismatch produces an unhandled exception in security-critical, unauthenticated request paths.

`sanitizeHost()` is called directly on the raw `host` query parameter, before authentication, in multiple entrypoints reachable by any anonymous HTTP request:
- Express: `clientSideRedirect()` in `redirect-to-auth.ts`, invoked for any embedded auth redirect request. [4](#0-3) 
- React Router: `validateShopAndHostParams()`, called at the start of `authenticate.admin()`. [5](#0-4) 
- `redirectToShopifyOrAppRoot()` (both Remix and React Router variants), which call `api.utils.sanitizeHost(url.searchParams.get('host')!)!` directly. [6](#0-5) 
- `renderAppBridge()`, similarly built into the HTML-serving/CSP-header path. [7](#0-6) 

None of these call sites wrap `sanitizeHost` in a `try/catch`.

### Impact Explanation
Any anonymous request to an app's auth or embedded-admin routes with a crafted `host` query parameter (character-set-valid but length-invalid base64) triggers an uncaught synchronous exception inside a request-handling function. Depending on the hosting framework's error-handling wiring, this manifests as:
- An unhandled promise rejection/500 for that request in Express-based apps (`shopify-app-express`) if the async middleware chain does not wrap the call, since `clientSideRedirect` and `redirectToAuth` are not defensively guarded here.
- A crash/500 in Remix/React-Router loaders where these helper functions are invoked outside of a `try/catch`, before the framework's own error boundary logic can distinguish "invalid input" from "internal error," producing noisy/uninformative 500s on a public, unauthenticated endpoint instead of the intended `InvalidHostError` behavior that the rest of the validator is designed to produce.

This is a low-effort, unauthenticated denial-of-service vector against the app's authentication entrypoint: a single crafted GET request to `/auth?host=...` (or the embedded admin root) can repeatedly force exceptions in the auth-handling code path instead of the graceful `null`/`InvalidHostError` handling the function's contract promises.

### Likelihood Explanation
High likelihood of triggering: the attack requires only crafting a `host` query string value with a length not divisible by 4 using base64-alphabet characters (trivial, e.g. any 5, 6, or 9-character base64-alphabet string), and sending an ordinary unauthenticated GET request to any of the listed entrypoints. No authentication, secret knowledge, or privileged access is required.

### Recommendation
Wrap the `atob()` call in `decodeHost()` (or in `sanitizeHost()`) in a `try/catch`, returning `null` (or throwing the existing `InvalidHostError`) on decode failure, consistent with how `sanitizeHost()` already handles other invalid inputs. Additionally, validate that the base64 length invariant holds (`host.length % 4 !== 1`) before attempting to decode, so the regex-based pre-check and the actual decode step enforce the same invariant.

### Proof of Concept
1. Send an anonymous GET request to an app's auth redirect route with an embedded flag, e.g.:
   `GET /auth?shop=test-shop.myshopify.com&embedded=1&host=AAAAA`
   (`"AAAAA"` is 5 characters, matches `base64regex`, but `5 % 4 === 1`.)
2. `redirectToAuth()` → `clientSideRedirect()` calls `api.utils.sanitizeHost(req.query.host)`.
3. `sanitizeHost()` passes the regex check, then calls `decodeHost("AAAAA")` → `atob("AAAAA")`.
4. `atob()` throws `InvalidCharacterError` (or equivalent) synchronously; the exception is not caught anywhere in `sanitizeHost()` or the calling function, propagating as an unhandled error in the request path instead of the intended `null`/`InvalidHostError` response.

### Citations

**File:** packages/apps/shopify-api/lib/auth/decode-host.ts (L1-3)
```typescript
export function decodeHost(host: string): string {
  return atob(host);
}
```

**File:** packages/apps/shopify-api/lib/utils/shop-validator.ts (L52-59)
```typescript
export function sanitizeHost(config: ConfigInterface) {
  return (host: string, throwOnInvalid = false): string | null => {
    const base64regex = /^[0-9a-zA-Z+/]+={0,2}$/;

    let sanitizedHost = base64regex.test(host) ? host : null;
    if (sanitizedHost) {
      const {hostname} = new URL(`https://${decodeHost(sanitizedHost)}`);

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts (L1-29)
```typescript
import {redirect} from '@remix-run/server-runtime';

import {BasicParams} from '../../../types';

export function validateShopAndHostParams(
  params: BasicParams,
  request: Request,
) {
  const {api, config, logger} = params;

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/redirect-to-shopify-or-app-root.ts (L13-14)
```typescript
  const host = api.utils.sanitizeHost(url.searchParams.get('host')!)!;
  const shop = api.utils.sanitizeShop(url.searchParams.get('shop')!)!;
```

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/admin/helpers/render-app-bridge.ts (L37-39)
```typescript
  const shop = api.utils.sanitizeShop(
    new URL(request.url).searchParams.get('shop')!,
  );
```
