### Title
Unhandled TypeError in `sanitizeHost` via crafted `host` param crashes the OAuth begin handler - ([File: packages/apps/shopify-api/lib/utils/shop-validator.ts])

### Summary
`sanitizeHost` (packages/apps/shopify-api/lib/utils/shop-validator.ts:52-90) passes the base64-decoded `host` query parameter directly into `new URL()` without a try/catch. A crafted base64 value that decodes into a malformed authority (e.g. an unbalanced `[`) makes the `URL` constructor throw a `TypeError`, and none of its callers on the unauthenticated `/auth` (begin) path — `redirectWithExitIframe` (packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/redirect-with-exitiframe.ts:15) or `validateShopAndHostParams` (packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts:21) — wrap the call in a try/catch, so the exception propagates uncaught.

### Finding Description
`sanitizeHost` is defined as: [1](#0-0) 

The `host` value is only checked against a base64 character-set regex before decoding; nothing validates that the decoded string forms a valid URL authority. `decodeHost` is a trivial `atob` wrapper: [2](#0-1) 

An attacker fully controls the `host` query parameter and can base64-encode any ASCII string (e.g. `[not-a-valid-authority`) that passes the base64 regex but produces an invalid URL authority. `new URL('https://[not-a-valid-authority')` throws a `TypeError: Invalid URL`, uncaught inside `sanitizeHost`.

On the `shopify-app-remix` OAuth begin path, `respondToOAuthRequests` routes unauthenticated `GET config.auth.path` requests into `handleAuthBeginRequest`: [3](#0-2) 

`handleAuthBeginRequest` calls `redirectWithExitIframe` when the request header `Sec-Fetch-Dest: iframe` is present — a header fully attacker-controlled since the request is not a browser-restricted fetch: [4](#0-3) 

`redirectWithExitIframe` calls `sanitizeHost` with no try/catch: [5](#0-4) 

Neither `handleAuthBeginRequest` nor `respondToOAuthRequests`'s `isAuthRequest` branch wraps this call in a try/catch (only `handleAuthCallbackRequest` for the callback path has a surrounding try/catch at packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts:191-222). Similarly, `validateShopAndHostParams`, used in the document-request/`ensureInstalledOnShop` path, calls `sanitizeHost` unguarded: [6](#0-5) 

Note that the OAuth **callback** path (`handleAuthCallbackRequest`) is not actually affected the way the question states, because any exception raised inside its try block (lines 191–222) is caught by `oauthCallbackError`, which returns a proper `Response` with status 500 rather than letting the exception propagate unhandled. The genuinely uncaught path is the `/auth` **begin** request via `redirectWithExitIframe`/`validateShopAndHostParams`, not `handleAuthCallbackRequest`.

### Impact Explanation
This produces a per-request unhandled exception (uncaught `TypeError`) in the OAuth begin handler instead of the library's normal `Response`-based error flow. Depending on the runtime adapter (e.g., Remix/Node/Cloudflare Workers), this could surface as an ugly 500 error page rather than a controlled error response, or in some server configurations could affect process stability for that request. This matches a low-to-moderate "Denial of Service in authentication handler" impact class — it does not permit token theft, session forgery, or cross-tenant access; it is limited to breaking the graceful-failure guarantee of the auth flow.

### Likelihood Explanation
No preconditions are required beyond knowledge of a valid shop domain (which is public/enumerable) — the attacker sends a single unauthenticated GET request to `config.auth.path` with `shop=<valid-shop>&host=<crafted-base64>&embedded=1` and header `Sec-Fetch-Dest: iframe`. This is fully repeatable and requires no secrets, cookies, or privileged role.

### Recommendation
Wrap the `new URL()` call in `sanitizeHost` in a try/catch, treating any parse failure as an invalid host (return `null` / throw `InvalidHostError` when `throwOnInvalid` is set), consistent with how `sanitizeShop` handles invalid input via regex matching rather than a throwing API.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/utils/__tests__/shop-validator.test.ts
import {sanitizeHost} from '../shop-validator';
import {testConfig} from '../../__tests__/test-config';

test('sanitizeHost throws uncaught TypeError on malformed decoded host', () => {
  const maliciousHost = Buffer.from('[not-a-valid-authority').toString('base64');
  // e.g. "W25vdC1hLXZhbGlkLWF1dGhvcml0eQ=="
  expect(() => sanitizeHost(testConfig())(maliciousHost, false)).toThrow(TypeError);
  // Currently throws "Invalid URL" TypeError instead of returning null
});
```
Equivalent HTTP-level PoC against a `shopify-app-remix` app:
```
GET /auth?shop=my-shop.myshopify.com&host=W25vdC1hLXZhbGlkLWF1dGhvcml0eQ%3D%3D&embedded=1 HTTP/1.1
Host: app.example.com
Sec-Fetch-Dest: iframe
```
Expected (buggy) result: unhandled `TypeError` thrown inside `redirectWithExitIframe` → `sanitizeHost`, not converted to a `Response`.
Expected (fixed) result: `sanitizeHost` returns `null`, and the flow proceeds without a `host` query param in the exit-iframe redirect (per the existing `if (host) {...}` branch in `redirect-with-exitiframe.ts`).

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts (L48-64)
```typescript
  public async respondToOAuthRequests(request: Request): Promise<void | never> {
    const {api, config} = this;

    const url = new URL(request.url);
    const isAuthRequest = url.pathname === config.auth.path;
    const isAuthCallbackRequest = url.pathname === config.auth.callbackPath;

    if (isAuthRequest || isAuthCallbackRequest) {
      const shop = api.utils.sanitizeShop(url.searchParams.get('shop')!);
      if (!shop) throw new Response('Shop param is invalid', {status: 400});

      if (isAuthRequest) {
        throw await this.handleAuthBeginRequest(request, shop);
      } else {
        throw await this.handleAuthCallbackRequest(request, shop);
      }
    }
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts (L163-181)
```typescript
  private async handleAuthBeginRequest(
    request: Request,
    shop: string,
  ): Promise<never> {
    const {api, config, logger} = this;

    logger.info('Handling OAuth begin request', {shop});

    // If we're loading from an iframe, we need to break out of it
    if (
      config.isEmbeddedApp &&
      request.headers.get('Sec-Fetch-Dest') === 'iframe'
    ) {
      logger.debug('Auth request in iframe detected, exiting iframe', {shop});
      throw redirectWithExitIframe({api, config, logger}, request, shop);
    } else {
      throw await beginAuth({api, config, logger}, request, false, shop);
    }
  }
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/redirect-with-exitiframe.ts (L5-16)
```typescript
export function redirectWithExitIframe(
  params: BasicParams,
  request: Request,
  shop: string,
): never {
  const {api, config} = params;
  const url = new URL(request.url);

  const queryParams = url.searchParams;

  const host = api.utils.sanitizeHost(queryParams.get('host')!);

```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts (L5-29)
```typescript
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
