### Title
Uncaught `TypeError` in `sanitizeHost` when base64-valid `host` decodes to an invalid URL authority - ([File: packages/apps/shopify-api/lib/utils/shop-validator.ts])

### Summary
`sanitizeHost` does not wrap the `new URL(`https://${decodeHost(sanitizedHost)}`)` call in a try/catch, so any attacker-supplied `host` value that is valid base64 (matches `base64regex`) but decodes into a string containing forbidden host code points (e.g. a space) causes `new URL()` to throw a raw `TypeError`, which propagates out of `sanitizeHost` instead of being converted into the intended `InvalidHostError`.

### Finding Description
`sanitizeHost` first validates the raw `host` param against `base64regex = /^[0-9a-zA-Z+/]+={0,2}$/` [1](#0-0) . If that passes, it unconditionally calls `decodeHost(sanitizedHost)` (a plain `atob`) and feeds the decoded bytes directly into `new URL(`https://${decoded}`)` with no surrounding try/catch [2](#0-1) . `atob` can decode arbitrary binary/control characters that are not restricted by `base64regex`, since the regex only constrains the *encoded* representation, not the decoded content. If the decoded string contains a character forbidden in a URL host (e.g. an embedded space), Node's WHATWG `URL` constructor throws `TypeError [ERR_INVALID_URL]` rather than returning gracefully. This throw happens before the `throwOnInvalid` check at line 85-87, so it happens regardless of whether the caller wants exceptions or a `null` return — meaning even callers who rely on `sanitizeHost(host)` (no throw) returning `null` on bad input will instead get an unhandled exception. This function is reachable from unauthenticated request-handling code paths such as `validateShopAndHostParams` in the Remix/React Router adapters, which reads `host` straight from the request's query string and passes it to `api.utils.sanitizeHost(...)` without any additional validation or try/catch [3](#0-2) , and from `buildEmbeddedAppUrl`/`getEmbeddedAppUrl`, which also passes attacker-controlled `host` query params straight through [4](#0-3) . None of the existing checks (base64 regex, `hostRegex` domain allowlist) validate that the decoded string is a syntactically valid URL authority before constructing the `URL` object, so this is a genuine gap in the library rather than a caller misuse issue.

### Impact Explanation
An unauthenticated attacker can trigger an unhandled `TypeError` inside the authentication/embedded-app URL resolution path merely by crafting a `host` query parameter, causing the request handler to crash instead of returning the intended controlled `InvalidHostError`/400 response. Depending on the host framework's default error handling, this can manifest as an uncaught exception/500 response, and in frameworks without a production error boundary, it can leak stack trace information. This matches the "DoS in an authentication handler" bounty impact class described in the rules.

### Likelihood Explanation
No privileges or secrets are required — a single unauthenticated request with a crafted `host` query parameter is sufficient, and the code path (`authenticate`/embedded app URL resolution) is hit before any session or HMAC validation. The only requirement is that the base64-encoded string decode to characters that are invalid in a URL authority (e.g. a space, or other forbidden host code points), which is trivial to construct. This makes the issue reliably and repeatably reproducible with a single crafted GET request.

### Recommendation
Wrap the `new URL(...)` call in `sanitizeHost` in a try/catch, and on failure treat the host as invalid: set `sanitizedHost = null` and, if `throwOnInvalid` is set, throw `InvalidHostError` instead of allowing the raw `TypeError` to propagate.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/utils/__tests__/shop-validator.test.ts
import {shopifyApi} from '../..';
import {testConfig} from '../../__tests__/test-config';
import {InvalidHostError} from '../../error';

test('sanitizeHost should not throw a raw TypeError for malformed decoded host', () => {
  const shopify = shopifyApi(testConfig());

  // decodes to "exa mple.myshopify.com/admin" -- the embedded space is a
  // forbidden host code point and makes `new URL()` throw TypeError.
  const craftedBase64 = Buffer.from(
    'exa mple.myshopify.com/admin',
  ).toString('base64');

  // Currently throws TypeError [ERR_INVALID_URL] instead of InvalidHostError
  expect(() => shopify.utils.sanitizeHost(craftedBase64, true)).toThrow(
    InvalidHostError,
  );

  // Non-throwing form should return null, not crash
  expect(shopify.utils.sanitizeHost(craftedBase64)).toBeNull();
});
```
Running this test against the current implementation fails because the thrown error is `TypeError [ERR_INVALID_URL]: Invalid URL`, not `InvalidHostError`, confirming the unhandled-exception path.

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts (L21-28)
```typescript
    const host = api.utils.sanitizeHost(url.searchParams.get('host')!);
    if (!host) {
      logger.debug('Invalid host, redirecting to login path', {
        shop,
        host: url.searchParams.get('host'),
      });
      throw redirectToLoginPath(request, params);
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
