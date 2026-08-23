### Title
`sanitizeHost()` throws an uncaught `TypeError` instead of returning null/`InvalidHostError` for malformed base64 `host` values - (File: packages/apps/shopify-api/lib/utils/shop-validator.ts)

### Summary
`sanitizeHost()` validates the `host` param with a base64-charset regex, then unconditionally calls `new URL(`https://${decodeHost(sanitizedHost)}`)` with no try/catch. An attacker can craft a base64 string (passing the charset check) that decodes to a value the WHATWG `URL` parser rejects (e.g. an unbalanced IPv6 bracket like `[::1`), causing `new URL()` to throw a raw `TypeError` instead of the function failing closed with `null`/`InvalidHostError`.

### Finding Description
`sanitizeHost` at [1](#0-0)  only checks that the raw `host` string matches the base64 character-set regex `/^[0-9a-zA-Z+/]+={0,2}$/`. That regex says nothing about the *decoded* content. It then feeds the decoded bytes directly into `new URL()` with no exception handling:
```
let sanitizedHost = base64regex.test(host) ? host : null;
if (sanitizedHost) {
  const {hostname} = new URL(`https://${decodeHost(sanitizedHost)}`);
  ...
```
`decodeHost` simply calls `atob(host)` [2](#0-1) . Because base64 can encode arbitrary bytes, an attacker fully controls the decoded string. A value such as `[::1` (unbalanced IPv6 literal bracket) is valid base64-charset input once encoded, but produces `https://[::1`, which the `URL` constructor throws a `TypeError` for (invalid IPv6 host syntax) rather than returning gracefully.

This function is called from multiple unauthenticated/pre-auth code paths with no surrounding try/catch:
- `buildEmbeddedAppUrl`/`getEmbeddedAppUrl`, which is reachable directly from a request's `host` query parameter [3](#0-2) .
- `validateShopAndHostParams` in the Remix/React Router adapters, invoked on the embedded-admin authentication path before a session exists [4](#0-3) .
- Redirect helpers in `shopify-app-express` and `shopify-app-react-router` that call `sanitizeHost` similarly.

None of these callers wrap the call in try/catch, and the function's documented contract (return `string | null`, or throw a typed `InvalidHostError` only when `throwOnInvalid` is set) is violated when `new URL()` throws a generic `TypeError` instead.

### Impact Explanation
This causes an unhandled exception on an authentication-adjacent, unauthenticated code path (`host` query parameter processing during OAuth begin / embedded app URL construction). Depending on the runtime/framework, this manifests as a 500 error for that request or, in some middleware stacks, an unhandled exception/promise rejection that can affect the process. This matches the "DoS in an authentication handler" impact class scoped to the audit rules — it does not enable session forgery or data access, only availability disruption for the specific request path.

### Likelihood Explanation
Trivial to trigger: any unauthenticated client can send `GET /auth?shop=...&host=<attacker-chosen-base64>` (or hit `getEmbeddedAppUrl`) with a base64 string that decodes to something like `[::1` or other unbalanced-bracket/control-byte sequences. No secrets, no privileged access, and no non-default configuration are required — this is a pure input-validation gap in the library's `sanitizeHost`.

### Recommendation
Wrap the `new URL()` construction in `sanitizeHost` in a try/catch, treating any parse failure the same as a regex mismatch (set `sanitizedHost = null`), before evaluating `throwOnInvalid`:
```ts
let sanitizedHost = base64regex.test(host) ? host : null;
if (sanitizedHost) {
  try {
    const {hostname} = new URL(`https://${decodeHost(sanitizedHost)}`);
    // ... existing hostRegex check
  } catch {
    sanitizedHost = null;
  }
}
```

### Proof of Concept
```ts
import {shopifyApi} from '../..';
import {testConfig} from '../../__tests__/test-config';
import {InvalidHostError} from '../../error';

test('sanitizeHost fails closed on malformed decoded host instead of throwing raw TypeError', () => {
  const shopify = shopifyApi(testConfig());

  const maliciousHost = Buffer.from('[::1').toString('base64');

  // Current behavior: throws a raw TypeError from the URL constructor
  // Expected (fixed) behavior: returns null, or throws InvalidHostError only when throwOnInvalid=true
  expect(shopify.utils.sanitizeHost(maliciousHost)).toBeNull();

  expect(() =>
    shopify.utils.sanitizeHost(maliciousHost, true),
  ).toThrow(InvalidHostError);
});
```
Running this against the current implementation shows the assertions fail because `new URL('https://[::1')` throws `TypeError: Invalid URL` uncaught inside `sanitizeHost`, rather than the function returning `null`/throwing `InvalidHostError`.

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts (L9-28)
```typescript
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
```
