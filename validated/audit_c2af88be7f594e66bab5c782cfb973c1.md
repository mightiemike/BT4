### Title
Unhandled exception in `sanitizeHost` from malformed base64 `host` parameter crashes the auth flow - ([File: packages/apps/shopify-api/lib/utils/shop-validator.ts])

### Summary
`sanitizeHost` validates the `host` query parameter with a regex that accepts base64 characters of *any* length, then passes the value straight to `atob()` via `decodeHost` without a try/catch. `atob()` (the WHATWG "forgiving-base64" decode) throws a `DOMException` whenever the input length (mod 4) is 1, which the regex does not exclude. This is directly analogous to the go-multiaddr bug: an under-validated parser accepts an input shape its own regex/length check doesn't fully cover, and the following unguarded call panics/throws on that specific malformed shape.

### Finding Description
`sanitizeHost` only checks the base64 character set, not that the string is a syntactically complete base64 blob: [1](#0-0) 

`decodeHost` simply calls the global `atob`: [2](#0-1) 

`atob` implements the forgiving-base64 decode algorithm, which throws `InvalidCharacterError` whenever, after stripping any trailing `=`, the remaining length mod 4 equals 1 (e.g. a single-character host value like `?host=A`, or any base64-alphabet string whose un-padded length is 4n+1). The regex `^[0-9a-zA-Z+/]+={0,2}$` happily accepts such strings because it never checks length modularity, so the throw is unguarded and propagates out of `sanitizeHost`.

This function is called directly, with no surrounding try/catch, from code that runs on every unauthenticated request to an embedded-app route before a session even exists: [3](#0-2) [4](#0-3) 

It is also reached via `getEmbeddedAppUrl`/`buildEmbeddedAppUrl`, again with no try/catch around the `sanitizeHost`/`decodeHost` calls: [5](#0-4) 

Both call sites are reachable by any anonymous request that supplies `?host=` with a valid-shop query and one specially-shaped `host` value — no authentication, cookies, or prior session are required.

### Impact Explanation
An anonymous request to any embedded-app admin route (e.g. `/?shop=<valid-shop>.myshopify.com&host=A`) with a `host` value whose base64 length modulo 4 is 1 causes `atob()` to throw inside `sanitizeHost`, which is not caught anywhere in the call chain (`validateShopAndHostParams` → `sanitizeHost`, or `getEmbeddedAppUrl` → `buildEmbeddedAppUrl` → `sanitizeHost`). This turns every request that would normally be handled gracefully (returning a login redirect / App Bridge page for an "invalid host") into an unhandled exception in the authentication entry point of the app, i.e. a DoS of the auth handler triggerable with a single, trivially-crafted HTTP request and no privileges — the same root-cause pattern as the go-multiaddr report (parser accepts a malformed edge-case shape that a downstream unguarded call cannot handle).

### Likelihood Explanation
High: triggering requires only one unauthenticated GET request with a crafted `host` query string; no handshake, secret, or victim cooperation is needed, and the vulnerable code sits directly in the request path shared by `shopify-app-remix`, `shopify-app-react-router`, and any caller of `getEmbeddedAppUrl`/`buildEmbeddedAppUrl` (used by `shopify-app-express` too).

### Recommendation
Wrap the `atob` call (or the whole body of `sanitizeHost`) in a try/catch and treat any decode failure as an invalid host (return `null` / throw `InvalidHostError` per `throwOnInvalid`), and/or tighten the regex to also enforce valid base64 length (`length % 4 !== 1`) before calling `decodeHost`.

### Proof of Concept
```
GET /?shop=test-shop.myshopify.com&host=A HTTP/1.1
Host: <app-host>
```
`host=A` passes `base64regex.test('A')` (single alphanumeric char, no padding required by the regex), then `decodeHost('A')` calls `atob('A')`, which throws `InvalidCharacterError` because the un-padded length (1) mod 4 equals 1. This exception is unhandled in `sanitizeHost`, `validateShopAndHostParams`, and `buildEmbeddedAppUrl`, crashing/500-ing the request in the authentication entry point for an anonymous, unauthenticated caller.

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
