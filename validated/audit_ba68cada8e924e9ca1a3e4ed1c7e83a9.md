### Title
Unhandled exception in `sanitizeHost` lets an unauthenticated attacker crash Shopify app auth/redirect handlers with a crafted `host` parameter - (File: packages/apps/shopify-api/lib/utils/shop-validator.ts)

### Summary
`sanitizeHost` is meant to validate the `host` query parameter and gracefully return `null` (or throw a well-defined `InvalidHostError`) for bad input. Instead, it first only checks that the string is syntactically valid base64, then blindly decodes it and passes the result straight into `new URL()` without a try/catch. Because base64-decoded (`atob`) output can contain arbitrary control characters or forbidden host code points, an attacker can construct a value that passes the base64 check but makes `new URL()` throw an uncaught `TypeError`. This propagates out of `sanitizeHost` as an unexpected, unhandled exception in multiple unauthenticated request-handling code paths (redirect-to-auth, embedded-app URL construction, shop/host param validation for `authenticate.admin`), instead of the expected controlled `InvalidHostError`/`null` result — directly analogous to the Angle Protocol finding where a small, unprivileged "donation" caused an unexpected revert deep in a helper used by a critical path (redemptions), bricking otherwise-valid requests.

### Finding Description
`sanitizeHost` in [1](#0-0)  only validates the raw string against a loose base64 character-set regex (`/^[0-9a-zA-Z+/]+={0,2}$/`) before calling `decodeHost` and constructing a `URL`: [2](#0-1) 

`decodeHost` is a thin wrapper over `atob`, which can decode to a string containing arbitrary bytes/control characters (e.g. `\u0000`, `\u0001`, or other forbidden host code points), since the base64 character-set check does not constrain what the decoded bytes look like: [3](#0-2) 

The WHATWG `URL` parser throws a `TypeError` ("Invalid URL") for hostnames containing forbidden host code points (control characters, spaces, certain punctuation, or values that fail IDNA processing). Because there is no try/catch around `new URL(`https://${decodeHost(sanitizedHost)}`)`, any such crafted-but-base64-valid `host` value causes `sanitizeHost` to throw an uncaught exception instead of returning `null`/throwing the intended `InvalidHostError`.

This function is invoked directly with attacker-controlled, unauthenticated request data in several places that do not wrap the call to catch generic exceptions:
- `getEmbeddedAppUrl`/`buildEmbeddedAppUrl`, which calls `sanitizeHost(config)(host, true)`: [4](#0-3) 
- `redirectToAuth`'s client-side redirect path in shopify-app-express, called from an anonymous OAuth-begin flow: [5](#0-4) 
- `validateShopAndHostParams`, used to gate the `authenticate.admin()` entry point in both the react-router and remix adapters, invoked on every embedded-app admin request before any session/auth check: [6](#0-5) , [7](#0-6) 
- `redirectWithExitIframe`, also reachable pre-auth: [8](#0-7) 

None of these call sites wrap `sanitizeHost` in a try/catch for arbitrary exceptions — they only check the return value against `null`/falsy, expecting a controlled failure mode, not a thrown `TypeError`.

### Impact Explanation
An unauthenticated attacker can hit any of the above entry points (which run before session/authentication checks, exactly like the "unprivileged donation" in the original report) with a crafted `host` query parameter that is valid base64 but decodes to a string with forbidden host code points. This causes `sanitizeHost` to throw an unhandled `TypeError` instead of returning `null`. Depending on the runtime (Express middleware without a final error handler, a Remix/React Router loader without an error boundary for this specific throw type, or a serverless single-request execution context), this can:
- Return an uncaught-exception 500 response instead of the intended graceful `InvalidHostError`/redirect-to-login flow, breaking the admin authentication and OAuth redirect flow for that request.
- In process models where unhandled synchronous throws inside a request handler are not caught by a global handler (e.g. some serverless adapters or the Express app if `redirectToAuth`/`validateShopAndHostParams` are invoked outside expected try/catch scaffolding), this can destabilize the whole worker/process, denying service to legitimate requests — mirroring the "any call to `_quoteRedemptionCurve` will also revert" DoS pattern in the original finding, here for the app's authentication/redirect entry points.

### Likelihood Explanation
High likelihood of triggering the crash: the attacker needs no authentication, no state, and only needs to control the `host` query parameter of a normal app request — something every embedded Shopify app admin route and OAuth flow processes from anonymous traffic. The precondition (a base64 string decoding to a byte sequence with forbidden host code points, e.g. a NUL byte) is trivial to construct.

### Recommendation
Wrap the `new URL(...)` construction in `sanitizeHost` in a try/catch, treating any parsing failure as "invalid host" (returning `null` or throwing the existing `InvalidHostError`), matching the function's documented contract:

```ts
export function sanitizeHost(config: ConfigInterface) {
  return (host: string, throwOnInvalid = false): string | null => {
    const base64regex = /^[0-9a-zA-Z+/]+={0,2}$/;
    let sanitizedHost = base64regex.test(host) ? host : null;
    if (sanitizedHost) {
      try {
        const {hostname} = new URL(`https://${decodeHost(sanitizedHost)}`);
        // ... existing regex checks
      } catch {
        sanitizedHost = null;
      }
    }
    if (!sanitizedHost && throwOnInvalid) {
      throw new InvalidHostError('Received invalid host argument');
    }
    return sanitizedHost;
  };
}
```

Additionally, audit all call sites (`getEmbeddedAppUrl`, `redirectToAuth`, `validateShopAndHostParams`, `redirectWithExitIframe`, etc.) to ensure they don't assume `sanitizeHost`/`sanitizeShop` can only fail via `null` return or the documented custom error types.

### Proof of Concept
```ts
// A host string that is valid base64 but decodes to bytes containing a
// forbidden host code point (NUL byte), which crashes new URL().
const maliciousHost = Buffer.from('\u0000.myshopify.com/admin', 'latin1').toString('base64');

// This throws an uncaught TypeError ("Invalid URL") instead of returning null
// or throwing InvalidHostError, in packages/apps/shopify-api/lib/utils/shop-validator.ts
shopify.utils.sanitizeHost(maliciousHost);
```
Sending this value as the `host` query parameter to any embedded-app admin route (e.g. `GET /?shop=test.myshopify.com&host=<maliciousHost>`) reaches `validateShopAndHostParams` → `api.utils.sanitizeHost(...)` and throws before the expected "invalid host" handling logic runs.

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

**File:** packages/apps/shopify-api/lib/auth/get-embedded-app-url.ts (L44-52)
```typescript
export function buildEmbeddedAppUrl(
  config: ConfigInterface,
): BuildEmbeddedAppUrl {
  return (host: string): string => {
    sanitizeHost(config)(host, true);
    const decodedHost = decodeHost(host);

    return `https://${decodedHost}/apps/${config.apiKey}`;
  };
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

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts (L1-29)
```typescript
import {BasicParams, AppDistribution} from '../../../types';

import {renderAppBridge} from './render-app-bridge';

export function validateShopAndHostParams(
  params: BasicParams,
  request: Request,
) {
  const {api, config, logger} = params;

  if (config.distribution !== AppDistribution.ShopifyAdmin) {
    const url = new URL(request.url);
    const shop = api.utils.sanitizeShop(url.searchParams.get('shop')!);
    if (!shop) {
      logger.debug('Missing or invalid shop, rendering App Bridge', {
        shop,
      });
      throw renderAppBridgeOrError(request, params);
    }

    const host = api.utils.sanitizeHost(url.searchParams.get('host')!);
    if (!host) {
      logger.debug('Invalid host, rendering App Bridge', {
        shop,
        host: url.searchParams.get('host'),
      });
      throw renderAppBridgeOrError(request, params);
    }
  }
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/redirect-with-exitiframe.ts (L1-30)
```typescript
import {redirect} from '@remix-run/server-runtime';

import type {BasicParams} from '../../../types';

export function redirectWithExitIframe(
  params: BasicParams,
  request: Request,
  shop: string,
): never {
  const {api, config} = params;
  const url = new URL(request.url);

  const queryParams = url.searchParams;

  const host = api.utils.sanitizeHost(queryParams.get('host')!);

  queryParams.set('shop', shop);

  let destination = `${config.auth.path}?shop=${shop}`;

  if (host) {
    queryParams.set('host', host);
    destination = `${destination}&host=${host}`;
  }
  queryParams.set('exitIframe', destination);

  throw redirect(`${config.auth.exitIframePath}?${queryParams.toString()}`);
}


```
