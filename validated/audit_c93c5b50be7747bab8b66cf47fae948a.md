### Title
Unhandled `atob()` panic on malformed base64 `host` parameter causes uncaught exception in shop/host validation - (File: `packages/apps/shopify-api/lib/utils/shop-validator.ts`)

### Summary
`sanitizeHost` is supposed to gracefully reject invalid `host` query-parameter values by returning `null` (or throwing a well-typed `InvalidHostError`), the same way Penumbra's clue-key expansion was supposed to fail gracefully instead of panicking on malformed attacker input. Instead, a length-only base64 pre-check lets malformed-but-charset-valid strings through to `atob()`, which throws a raw, un-typed `DOMException`/`InvalidCharacterError` that is never caught in the call chain.

### Finding Description
`sanitizeHost` first checks the `host` value against a loose regex that validates only the character set, not proper base64 padding/length: [1](#0-0) 

For an input like `"A"` (length 1), this regex matches (`[0-9a-zA-Z+/]+={0,2}` accepts a single alphanumeric char with zero padding). The code then calls `decodeHost(sanitizedHost)`: [2](#0-1) 

`decodeHost` is a thin wrapper around the global `atob()`. Per the WHATWG forgiving-base64 algorithm that `atob` implements, an input whose length mod 4 equals 1 (e.g., length 1, 5, 9, …) causes `atob` to throw `InvalidCharacterError` rather than returning a decoded string. `sanitizeHost` has no `try/catch` around this call, so the exception propagates synchronously out of `sanitizeHost` instead of being turned into a `null` return value or a typed `InvalidHostError`.

This is directly analogous to the Penumbra bug class: an untrusted, attacker-supplied value (the `host` query parameter, fully controlled by an anonymous HTTP requester) is fed into a decode/expand routine that is expected to fail gracefully but instead panics with an unhandled low-level exception.

`sanitizeHost` is called from multiple unauthenticated, request-facing code paths that have no surrounding error handling for this specific throw:
- `buildEmbeddedAppUrl` / `getEmbeddedAppUrl`, reachable pre-authentication from any request with a `host` param: [3](#0-2) 
- `validateShopAndHostParams`, called at the very start of `authenticate.admin()` in both the Remix and React Router adapters, before any session/auth check: [4](#0-3) [5](#0-4) 
- `redirectToShopifyOrAppRoot`, which force-unwraps the result with `!`: [6](#0-5) 
- `shopify-app-express`'s `redirect-to-auth.ts` and `redirect-to-shopify-or-app-root.ts` middleware, invoked on unauthenticated OAuth entry/redirect requests: [7](#0-6) [8](#0-7) 

None of these callers catch the specific `DOMException` thrown by `atob`; they only expect `sanitizeHost` to return `null` or throw the documented `InvalidHostError`.

### Impact Explanation
An anonymous request supplying a crafted `host` query parameter of a length that is valid per the char-class regex but invalid per real base64 padding rules (e.g., `host=A`, or any base64-charset string whose length mod 4 is 1) triggers an unhandled exception deep inside shop/host validation instead of the expected `null`/`InvalidHostError` outcome. Depending on the runtime/framework wrapping (Express vs. Remix/React Router loader), this can surface as an unhandled promise rejection or an uncaught synchronous exception in a request-handling path that callers did not design to catch this class of error, producing inconsistent error handling and potential request-handler crashes/DoS on the affected route, rather than a clean, typed rejection.

### Likelihood Explanation
High: the `host` parameter is fully attacker-controlled and reachable from anonymous HTTP requests before any authentication step (e.g., embedded-app entry, `redirect-to-auth`, `redirect-to-shopify-or-app-root`). No secret, session, or privileged action is required to trigger it — an attacker simply needs to send a single crafted query parameter.

### Recommendation
Wrap the `atob()`/`decodeHost` call inside `sanitizeHost` (and any other direct callers of `decodeHost`) in a `try/catch`, converting decode failures into the same `null` (or `InvalidHostError` when `throwOnInvalid` is set) result path already used for other invalid-host cases, rather than letting `atob`'s raw `DOMException` propagate. Additionally, tighten the pre-check regex to validate real base64 length/padding rules (length mod 4 ∈ {0,2,3} with correct `=` padding) rather than only character class.

### Proof of Concept
```ts
import {shopifyApi} from '@shopify/shopify-api';
import {testConfig} from '.../shopify-api/lib/__tests__/test-config';

const shopify = shopifyApi(testConfig());

// "A" passes the char-class regex (/^[0-9a-zA-Z+/]+={0,2}$/)
// but has length 1 (mod 4 === 1), which is invalid base64 and
// causes the global atob() to throw InvalidCharacterError.
shopify.utils.sanitizeHost('A');
// -> throws an uncaught DOMException instead of returning null
```
Note: I was unable to execute this in a live environment to confirm the exact exception type/behavior across all target JS runtimes (Node's `atob` vs. browser/edge implementations); this is based on the documented WHATWG forgiving-base64 decode algorithm that `atob` implementations follow. A Devin session with code execution access would be needed to confirm the runtime-specific throw and its propagation behavior in each adapter (Express/Remix/React Router).

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts (L1-30)
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
}
```

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts (L1-30)
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
}
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/redirect-to-shopify-or-app-root.ts (L13-14)
```typescript
  const host = api.utils.sanitizeHost(url.searchParams.get('host')!)!;
  const shop = api.utils.sanitizeShop(url.searchParams.get('shop')!)!;
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

**File:** packages/apps/shopify-app-express/src/middlewares/redirect-to-shopify-or-app-root.ts (L22-22)
```typescript
      const host = api.utils.sanitizeHost(req.query.host as string)!;
```
