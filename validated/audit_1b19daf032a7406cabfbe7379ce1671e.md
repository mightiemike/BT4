### Title
`sanitizeHost`/`decodeHost` can throw an uncaught exception on malformed-but-regex-valid `host` values, crashing unauthenticated auth-flow handlers - ([File: packages/apps/shopify-api/lib/utils/shop-validator.ts])

### Summary
`sanitizeHost` validates the `host` query parameter using a loose regex before calling `atob()` (via `decodeHost`) to decode it. The regex does not enforce that the base64 string has a valid length, so a value such as a single character (`host=a`) passes the regex check but causes `atob()` to throw a `SyntaxError`/`DOMException` instead of the function gracefully returning `null`. Multiple unauthenticated/anonymous auth-flow entry points call `sanitizeHost` (and the related `getEmbeddedAppUrl`/`buildEmbeddedAppUrl`) without a surrounding try/catch, so this uncaught throw propagates out of the request handler.

### Finding Description
`sanitizeHost` is implemented as: [1](#0-0) 

The regex `^[0-9a-zA-Z+/]+={0,2}$` only checks the character set and optional padding, not that the string length (mod 4) is decodable. `decodeHost` performs no validation or exception handling itself: [2](#0-1) 

Passing a `host` value like `"a"` (length 1, valid characters, no padding needed to satisfy the regex) is accepted by the regex, but `atob("a")` throws, because a base64 string of length ≡ 1 (mod 4) cannot represent a whole number of bytes. This exception occurs inside `sanitizeHost`, well before any `try/catch`, and is not converted into the expected `null` return value or `InvalidHostError`.

This is directly analogous to the reported ERC4626 bug: an unchecked/insufficiently-validated precondition (`previewDeposit` returning 0 due to integer division truncation) causes a low-level primitive (`require`) to revert instead of the higher-level function handling the edge case gracefully. Here, an unchecked precondition (base64 length validity) causes a low-level primitive (`atob`) to throw instead of the higher-level `sanitizeHost` handling the edge case gracefully (returning `null`).

Multiple unprivileged/anonymous entry points call this function directly:
- `getEmbeddedAppUrl`/`buildEmbeddedAppUrl`, which call `sanitizeHost(config)(host, true)` then unconditionally `decodeHost(host)` with no catch: [3](#0-2) 
- `redirectToShopifyOrAppRoot` (Express), reachable after any successful OAuth callback redirect, with the `host` value taken straight from the query string: [4](#0-3) 
- `clientSideRedirect` in `redirect-to-auth.ts` (Express), reachable by any anonymous request that starts OAuth with `embedded=1`: [5](#0-4) 
- `validateShopAndHostParams` in both the Remix and React Router packages, called at the very start of `authenticate.admin()`, i.e. before any session/auth check, for every embedded app request: [6](#0-5) [7](#0-6) 
- `redirectWithExitIframe` (Remix), also unauthenticated-reachable: [8](#0-7) 

None of these callers wrap the `sanitizeHost`/`decodeHost` call in a try/catch, and the existing test suite for `sanitizeHost` only exercises regex-rejection cases (non-base64 characters, tampered suffix, wrong domain) — it does not test the "passes regex but fails `atob`" edge case: [9](#0-8) 

### Impact Explanation
Since `host` is an unauthenticated query parameter present on essentially every embedded-app request (OAuth begin/callback, `authenticate.admin()` entry, exit-iframe redirect), an attacker can send `?shop=<valid-shop>&host=a` (or any malformed-length base64-looking value) to these routes and trigger an unhandled exception at the very entry of an authentication/redirect handler. Depending on the hosting framework's error boundary, this manifests as an uncaught 500 at minimum, and in Express-style deployments without a global error handler wrapping these specific middlewares, it can break the merchant-facing install/auth flow entirely for that request. This matches the "DoS of an auth handler" acceptance criterion: unlike the original Solidity report (financially expensive to trigger), this variant is trivially and repeatedly triggerable by any unauthenticated client with zero cost, against handlers that are supposed to gracefully reject malformed input.

### Likelihood Explanation
High likelihood of triggering the crash (any single-character or otherwise mod-4-invalid base64-looking string works, with no cost or rate limit), but the security-relevant consequence is limited to an unhandled exception/500 response rather than session compromise, cross-tenant access, or credential leakage — similar in class/severity to the original acknowledged-but-low-impact finding.

### Recommendation
Wrap the `atob()` call in `decodeHost` (or the `new URL()`/`atob` calls inside `sanitizeHost`) in a try/catch, treating any decoding failure as an invalid host (returning `null`, or throwing `InvalidHostError` when `throwOnInvalid` is set), consistent with how other malformed-host cases are already handled. Additionally, tighten the regex to validate base64 length constraints (e.g., require the total length to be a multiple of 4, or explicitly reject lengths ≡ 1 mod 4) before attempting to decode.

### Proof of Concept
1. Send a GET request to any embedded-app auth entry point with a malformed `host`, e.g.:
   `GET /auth?shop=my-shop.myshopify.com&host=a`
   or, for the Remix/React Router `authenticate.admin()` entry point:
   `GET /app?shop=my-shop.myshopify.com&host=a&embedded=1`
2. `validateShopAndHostParams`/`redirectToShopifyOrAppRoot`/`buildEmbeddedAppUrl` calls `api.utils.sanitizeHost('a', ...)`.
3. Inside `sanitizeHost`, `base64regex.test('a')` returns `true` (single alphanumeric character, no padding required), so `decodeHost('a')` is invoked.
4. `decodeHost` calls `atob('a')`, which throws `SyntaxError: The string to be decoded is not correctly encoded.` (or `DOMException`, depending on runtime), because the base64 string is not a valid multiple-of-4 length.
5. Since no caller wraps this call in a try/catch, the exception propagates unhandled out of the auth handler instead of returning `null`/`InvalidHostError` as the surrounding code paths expect.

### Citations

**File:** packages/apps/shopify-api/lib/utils/shop-validator.ts (L52-66)
```typescript
export function sanitizeHost(config: ConfigInterface) {
  return (host: string, throwOnInvalid = false): string | null => {
    const base64regex = /^[0-9a-zA-Z+/]+={0,2}$/;

    let sanitizedHost = base64regex.test(host) ? host : null;
    if (sanitizedHost) {
      const {hostname} = new URL(`https://${decodeHost(sanitizedHost)}`);

      const originsRegex = [
        'myshopify\\.com',
        'shopify\\.com',
        'myshopify\\.io',
        'spin\\.dev',
        'shop\\.dev',
      ];
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

**File:** packages/apps/shopify-app-express/src/middlewares/redirect-to-shopify-or-app-root.ts (L22-30)
```typescript
      const host = api.utils.sanitizeHost(req.query.host as string)!;
      const redirectUrl = api.config.isEmbeddedApp
        ? await api.auth.getEmbeddedAppUrl({
            rawRequest: req,
            rawResponse: res,
          })
        : `/?shop=${res.locals.shopify.session.shop}&host=${encodeURIComponent(
            host,
          )}`;
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

**File:** packages/apps/shopify-api/lib/utils/__tests__/shop-validator.test.ts (L33-50)
```typescript
const INVALID_HOSTS = [
  {
    testhost: 'plain-string-is-not-base64',
    base64host: 'plain-string-is-not-base64',
  },
  {
    testhost: "valid host but ending with '-nope'",
    base64host: `${Buffer.from('my-other-host.myshopify.com/admin').toString(
      'base64',
    )}-nope`,
  },
  {
    testhost: 'my-fake-host.notshopify.com/admin',
    base64host: Buffer.from('my-fake-host.notshopify.com/admin').toString(
      'base64',
    ),
  },
];
```
