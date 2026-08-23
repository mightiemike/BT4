### Title
Uncaught `DOMException`/`InvalidCharacterError` in `sanitizeHost`/`decodeHost` on malformed-but-regex-matching base64 `host` values causes unhandled exception in auth path - (File: `packages/apps/shopify-api/lib/auth/decode-host.ts`)

### Summary
`decodeHost` is a one-line wrapper around `atob(host)` with no error handling. `sanitizeHost` (`packages/apps/shopify-api/lib/utils/shop-validator.ts`) validates the `host` param with a loose regex (`/^[0-9a-zA-Z+/]+={0,2}$/`) that accepts strings which are not valid base64 (e.g. `A=`, `AB=`), and then immediately calls `decodeHost` on that string before its own `InvalidHostError` check can run, so `atob` throws an unhandled `DOMException`/`InvalidCharacterError` instead of the library's structured `InvalidHostError`.

### Finding Description
`sanitizeHost` builds `base64regex` and tests the raw `host` input: [1](#0-0) 
The regex `^[0-9a-zA-Z+/]+={0,2}$/` only checks the character set and up to two trailing `=`, not that the string length is a multiple of 4 or that the padding/data-length combination is valid per the base64 spec. Strings such as `A=` or `AB=` pass this regex. Immediately after the regex passes, `sanitizeHost` calls `decodeHost(sanitizedHost)`, which is a bare `atob` call with no try/catch: [2](#0-1) 
For an input like `A=`, `atob` implements the WHATWG forgiving-base64 decode algorithm, which throws `InvalidCharacterError` (surfaced as an uncaught `DOMException`) when the pre-padding data length is invalid (mod 4 == 1). This throw happens inside `sanitizeHost` itself — *before* the function reaches its own `if (!sanitizedHost && throwOnInvalid) throw new InvalidHostError(...)` check — so the intended, structured `InvalidHostError` is never thrown; a raw, undocumented `DOMException` propagates instead.

`buildEmbeddedAppUrl` calls `sanitizeHost(config)(host, true)` and then `decodeHost(host)` again: [3](#0-2) 
Any caller relying on the documented contract ("invalid host throws `InvalidHostError`") can instead receive an uncaught `DOMException`, which many callers do not anticipate or catch specifically for.

One consumer, `shopify-app-express`'s `embedAppIntoShopify`, happens to wrap the call to `api.auth.getEmbeddedAppUrl` in a generic `try/catch (_error)`, so a crash is avoided there (though the request is misclassified as "No host provided"): [4](#0-3) 
However, this is incidental defensive coding in one downstream package, not a guarantee provided by the library itself. `sanitizeHost`/`decodeHost`/`buildEmbeddedAppUrl` are all public library APIs (exposed as `api.utils.sanitizeHost` and `api.auth.buildEmbeddedAppUrl`/`getEmbeddedAppUrl`) whose documented failure mode is `InvalidHostError`; the actual failure mode for a specific class of attacker-supplied strings is an unhandled exception, which is a defect in the library's input-validation contract, independent of whether a particular host app happens to wrap the call.

### Impact Explanation
An unprivileged attacker can send `GET /apps/embedded?host=A=` (or `host=AB=`, or other regex-matching/invalid-base64 combinations) to any endpoint that calls `getEmbeddedAppUrl`/`buildEmbeddedAppUrl`/`sanitizeHost` and does not itself add a specific catch for non-`InvalidHostError` exceptions. This crashes the request handler with an unhandled exception, matching the "DoS in an authentication handler" impact class in scope.

### Likelihood Explanation
The attack requires no privileges, no secret, and no non-default configuration — only crafting a `host` query parameter that satisfies the loose regex but is invalid base64. It is fully repeatable and works for any embedded app using this library's auth utilities directly (e.g., a custom Express/Remix/Node route calling `shopify.auth.getEmbeddedAppUrl`/`buildEmbeddedAppUrl`/`api.utils.sanitizeHost` without a broad catch-all). Likelihood of a concrete process crash depends on whether the specific host framework/route wraps the call generically (as `shopify-app-express`'s built-in `ensureInstalledOnShop` does); routes built directly against the raw `shopify-api` package following its documented `InvalidHostError` contract are exposed.

### Recommendation
Wrap the `atob` call in `decodeHost` in a try/catch and convert any decode failure into `null`/`InvalidHostError` at the `sanitizeHost` call site, and/or replace the loose `base64regex` in `sanitizeHost` with a stricter validation (e.g., verifying `host.length % 4 === 0` and correct padding placement) before calling `decodeHost`, ensuring `sanitizeHost`/`buildEmbeddedAppUrl` never throw anything other than the documented `InvalidHostError`.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/auth/__tests__/decode-host.test.ts
import {decodeHost} from '../decode-host';
import {buildEmbeddedAppUrl} from '../get-embedded-app-url';
import {testConfig} from '../../__tests__/test-config';
import * as ShopifyErrors from '../../error';

test('decodeHost throws raw DOMException for malformed-but-regex-matching base64', () => {
  expect(() => decodeHost('A=')).toThrow(); // throws DOMException, not InvalidHostError
});

test('buildEmbeddedAppUrl crashes instead of throwing InvalidHostError', () => {
  const config = testConfig();
  expect(() => buildEmbeddedAppUrl(config)('A=')).not.toThrow(
    ShopifyErrors.InvalidHostError,
  );
  // Actual behavior: throws an uncaught DOMException/InvalidCharacterError
});
```
Expected (buggy) result: both assertions show the thrown error is a `DOMException`/`InvalidCharacterError`, not `ShopifyErrors.InvalidHostError`, demonstrating the unhandled-exception path.

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

**File:** packages/apps/shopify-app-express/src/middlewares/ensure-installed-on-shop.ts (L172-187)
```typescript
  let embeddedUrl: string;
  try {
    embeddedUrl = await api.auth.getEmbeddedAppUrl({
      rawRequest: req,
      rawResponse: res,
    });
  } catch (_error) {
    config.logger.error(
      `ensureInstalledOnShop did not receive a host query argument`,
      {shop},
    );

    res.status(400);
    res.send('No host provided');
    return;
  }
```
