### Title
Malformed `host` query parameter crashes `sanitizeHost`/`decodeHost` with an uncaught exception, causing a DoS of the embedded-admin auth handler - (File: `packages/apps/shopify-api/lib/utils/shop-validator.ts`)

### Summary
`sanitizeHost` is meant to safely validate the anonymous, attacker-controlled `host` query parameter before an embedded app is authenticated. Its regex check does not enforce that the string is a *structurally valid* Base64 value (correct length/padding), so a crafted `host` value can pass the regex but make the underlying `atob()` call throw an uncaught exception. Because none of the unprivileged callers of `sanitizeHost` (`validateShopAndHostParams`, `redirectToAuth`, `getEmbeddedAppUrl`) wrap the call in a try/catch, this throws all the way out of the request handler, this is directly analogous to the reported bug class: a value that passes surface-level parameter validation reaches a low-level primitive (`faceValueRemaining`/`atob`) that reverts/throws on specific crafted input, breaking the entire flow (bond distribution / auth request handling) for every subsequent call in that code path.

### Finding Description
`sanitizeHost` validates the `host` value with a permissive regex and then unconditionally decodes it: [1](#0-0) 

```ts
export function sanitizeHost(config: ConfigInterface) {
  return (host: string, throwOnInvalid = false): string | null => {
    const base64regex = /^[0-9a-zA-Z+/]+={0,2}$/;

    let sanitizedHost = base64regex.test(host) ? host : null;
    if (sanitizedHost) {
      const {hostname} = new URL(`https://${decodeHost(sanitizedHost)}`);
      ...
```

`base64regex` only checks the *character set*, not that the string length is a multiple of 4 or that padding is correctly placed. `decodeHost` calls `atob(host)` directly with no error handling: [2](#0-1) 

`atob` throws a `DOMException`/`InvalidCharacterError` for base64 strings whose length is not a multiple of 4 or whose padding is malformed. Since the regex accepts strings like a single letter, three characters, or characters with `=` in the middle-of-length combos that regex still matches (`={0,2}$` only anchors trailing `=`, but does not require total length % 4 == 0), an attacker can supply a `host` value that passes the regex yet is invalid Base64, causing `atob` to throw synchronously inside `sanitizeHost`.

None of the unprivileged callers guard this call:
- `validateShopAndHostParams` (Remix/React Router) calls `api.utils.sanitizeHost(...)` directly, with no try/catch, as part of the very first embedded-admin request validation step, before any session/auth check: [3](#0-2) 
- `redirectToAuth`'s `clientSideRedirect` (Express) also calls `api.utils.sanitizeHost(req.query.host as string)` unguarded: [4](#0-3) 
- `getEmbeddedAppUrl`/`buildEmbeddedAppUrl` also call `sanitizeHost(config)(host, true)` unguarded: [5](#0-4) 

Because these are the first checks performed for an anonymous embedded-app request (no shop/host validation happens before them), the thrown exception is not converted into a controlled redirect or 4xx response — it becomes an unhandled runtime error in the request lifecycle. Depending on the host framework (Express, Remix, React Router), this manifests as an unhandled promise rejection or a generic 500 error, but the point of note is it bypasses the app's intended "redirect to login/App Bridge" flow entirely, and can be triggered repeatedly by an anonymous requester with no valid session or shop.

### Impact Explanation
This is a denial-of-service style flaw in the earliest stage of the embedded-app authentication pipeline. An attacker sending forged/anonymous requests with a malformed `host` parameter to `/auth`, the app root, or any route protected by `shopify.authenticate.admin`/`validateShopAndHostParams` can force an uncaught exception instead of the intended graceful redirect-to-login behavior. Depending on deployment (serverless/edge runtimes, or a Node process without top-level exception handling around the request), this can crash the process or leave the request hanging, denying service to legitimate users of that route. It does not by itself lead to cross-tenant data access, token theft, or forged sessions — its impact is a DoS of an authentication-adjacent handler.

### Likelihood Explanation
Likelihood is high in terms of reachability: `host` is a normal query parameter that any anonymous requester (not just Shopify) supplies, and `sanitizeHost` is invoked unconditionally on it before any session/authentication check in `validateShopAndHostParams`, `redirectToAuth`, and `getEmbeddedAppUrl`. Crafting a string that passes `/^[0-9a-zA-Z+/]+={0,2}$/` but is invalid Base64 (e.g., a 1-3 character alphanumeric string with no padding, such as `host=a` or `host=abc`) is trivial and requires no secrets or special access.

### Recommendation
- In `decodeHost` (`packages/apps/shopify-api/lib/auth/decode-host.ts`), wrap the `atob(host)` call in a try/catch and treat decode failures the same as "invalid host" (return `null`/throw `InvalidHostError` consistently with the `throwOnInvalid` contract), rather than letting the raw `DOMException` propagate.
- In `sanitizeHost` (`packages/apps/shopify-api/lib/utils/shop-validator.ts`), tighten the regex to require a length that is a correct multiple of 4 with valid padding placement, and/or catch decoding errors there directly and route them into the existing `InvalidHostError`/`null` handling.
- Audit all unprivileged callers (`validateShopAndHostParams`, `redirectToAuth`, `getEmbeddedAppUrl`) to confirm they always receive a controlled `InvalidHostError` (or `null`) rather than an unhandled runtime exception, and add regression tests using malformed-but-regex-matching Base64 strings (e.g., odd lengths, single characters, misplaced `=`).

### Proof of Concept
```
GET /auth?shop=test-shop.myshopify.com&host=a
```
1. `validateShopAndHostParams` (or `redirectToAuth`'s client-side branch) extracts `host=a` from the query string and calls `api.utils.sanitizeHost('a')`.
2. `base64regex.test('a')` returns `true` (single alphanumeric char, no `=` required), so `sanitizedHost = 'a'`.
3. `decodeHost('a')` calls `atob('a')`, which throws `InvalidCharacterError` because `'a'` is not a validly padded Base64 string.
4. The exception propagates out of `sanitizeHost` uncaught, since none of the call sites (`validate-shop-and-host-params.ts`, `redirect-to-auth.ts`, `get-embedded-app-url.ts`) wrap the call in error handling, aborting normal request processing instead of returning the intended login-page redirect or 400 response.

*Note: I was unable to execute this against a live/running instance of the repo to confirm the exact resulting HTTP status/error surface for each framework adapter (Express vs. Remix vs. React Router); the code-level control-flow analysis above shows the exception is unguarded, but exact framework-level error-handling behavior (e.g., Remix's default error boundary vs. an unhandled Node exception) may vary and would need runtime verification.*

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

**File:** packages/apps/shopify-api/lib/auth/get-embedded-app-url.ts (L44-53)
```typescript
export function buildEmbeddedAppUrl(
  config: ConfigInterface,
): BuildEmbeddedAppUrl {
  return (host: string): string => {
    sanitizeHost(config)(host, true);
    const decodedHost = decodeHost(host);

    return `https://${decodedHost}/apps/${config.apiKey}`;
  };
}
```
