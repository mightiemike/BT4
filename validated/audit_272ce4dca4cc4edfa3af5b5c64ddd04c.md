### Title
Uncaught `TypeError` in `sanitizeHost` from malformed base64-decoded host crashes auth-redirect handlers - (File: `packages/apps/shopify-api/lib/utils/shop-validator.ts`)

### Summary
`sanitizeHost` decodes an attacker-controlled base64 `host` query parameter and passes the raw decoded string directly into `new URL(\`https://${decodeHost(sanitizedHost)}\`)` without a try/catch. [1](#0-0)  If the decoded content is not parseable as a URL authority (e.g. an unterminated IPv6-literal bracket `[`), the `URL` constructor throws synchronously, and that exception propagates out of `sanitizeHost` uncaught instead of the function returning `null`.

### Finding Description
`sanitizeHost` only verifies that the raw input matches the base64 character alphabet (`/^[0-9a-zA-Z+/]+={0,2}$/`), which says nothing about the validity of the *decoded* content as a URL host. [2](#0-1)  After confirming the base64 shape, it immediately calls `decodeHost` (a thin wrapper over `atob`) and feeds the result into `new URL(...)` with no surrounding try/catch. [3](#0-2)  Supplying base64 for a string such as `"["` decodes back to `"["`, and `new URL('https://[')` throws a `TypeError: Invalid URL` (unterminated IPv6 host literal). That throw is not caught anywhere in `sanitizeHost`, so it bubbles up through every caller.

This function is reachable directly from unauthenticated HTTP requests: `buildEmbeddedAppUrl`/`getEmbeddedAppUrl` read `host` straight from the query string and call `sanitizeHost(config)(host, true)` with no wrapping try/catch, [4](#0-3)  and the Express/Remix helper `redirectToShopifyOrAppRoot` calls `api.utils.sanitizeHost(...)` on the raw query/search param without a try/catch as well. [5](#0-4) [6](#0-5)  None of these call sites, nor `sanitizeHost` itself, guard against the `URL` constructor throwing, so the exception is uncaught at the point it's raised.

### Impact Explanation
This is a denial-of-service against a single request path, not necessarily the whole server process. In Express, synchronous throws inside a route handler/middleware are caught automatically by Express 4's dispatcher and routed to error-handling middleware, resulting in a 500 response rather than a process crash — unless the handler is async and the throw isn't awaited/caught, which would produce an unhandled promise rejection (depends on Node's `unhandledRejection` handling and whether the app has a global handler; by default this can crash the Node process). In Remix/react-router, an uncaught throw from a loader/action is normally caught by the framework and rendered via error boundaries, again a scoped 500 rather than a full-process crash. So the concrete, verifiable impact is an unhandled exception/500 error on the auth-redirect or embedded-app-URL endpoint per malicious request, with a *potential* (but framework/runtime-dependent, not confirmed here) escalation to a Node process crash for async contexts where the rejection isn't handled — I was not able to trace all runtime wiring (e.g., whether `shopify-app-express`'s Express app registers a catch-all error handler for async middleware) to confirm process-level crash versus a contained 500.

### Likelihood Explanation
Trivial and fully unauthenticated: any anonymous client can send `?host=<base64 of "[">` to the embedded-app-URL/auth-redirect endpoints of any app built with this library, with no OAuth, HMAC, or session-token needed to reach `sanitizeHost`. It is deterministic and repeatable on every request.

### Recommendation
Wrap the `new URL(...)` call in `sanitizeHost` in a try/catch, returning `null` (or throwing `InvalidHostError` only when `throwOnInvalid` is set) on parse failure, matching the function's documented "return null for invalid host" contract instead of allowing raw `URL` parsing exceptions to escape.

### Proof of Concept
```javascript
// packages/apps/shopify-api/lib/utils/__tests__/shop-validator.test.ts
import {shopifyApi} from '../..';
import {testConfig} from '../../__tests__/test-config';

test('sanitizeHost throws instead of returning null for malformed decoded host', () => {
  const shopify = shopifyApi(testConfig());
  const maliciousHost = Buffer.from('[').toString('base64'); // "Ww=="

  // Expected (per contract): should return null
  // Actual: throws TypeError: Invalid URL, escaping sanitizeHost uncaught
  expect(() => shopify.utils.sanitizeHost(maliciousHost)).toThrow(TypeError);
});
```
Equivalent HTTP-level PoC: `GET /?shop=test.myshopify.com&host=Ww==` against any route that calls `getEmbeddedAppUrl`/`buildEmbeddedAppUrl` or `redirectToShopifyOrAppRoot` triggers the same uncaught `TypeError` from `new URL('https://[')`.

### Citations

**File:** packages/apps/shopify-api/lib/utils/shop-validator.ts (L52-59)
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

**File:** packages/apps/shopify-api/lib/auth/get-embedded-app-url.ts (L30-52)
```typescript

    const url = new URL(request.url, `https://${request.headers.host}`);
    const host = url.searchParams.get('host');

    if (typeof host !== 'string') {
      throw new ShopifyErrors.InvalidRequestError(
        'Request does not contain a host query parameter',
      );
    }

    return buildEmbeddedAppUrl(config)(host);
  };
}

export function buildEmbeddedAppUrl(
  config: ConfigInterface,
): BuildEmbeddedAppUrl {
  return (host: string): string => {
    sanitizeHost(config)(host, true);
    const decodedHost = decodeHost(host);

    return `https://${decodedHost}/apps/${config.apiKey}`;
  };
```

**File:** packages/apps/shopify-app-express/src/middlewares/redirect-to-shopify-or-app-root.ts (L22-22)
```typescript
      const host = api.utils.sanitizeHost(req.query.host as string)!;
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/redirect-to-shopify-or-app-root.ts (L13-13)
```typescript
  const host = api.utils.sanitizeHost(url.searchParams.get('host')!)!;
```
