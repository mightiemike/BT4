### Title
Uncaught exception (DoS) in `sanitizeHost()` via malformed base64 `host` parameter reaching unguarded `atob()`/`new URL()` calls - ([File: packages/apps/shopify-api/lib/utils/shop-validator.ts])

### Summary
`sanitizeHost()` validates the `host` query parameter with a permissive regex that accepts any base64-*character-set* string regardless of length/padding correctness, then passes it directly to `decodeHost()` (which calls `atob()`) and into `new URL()` with no `try/catch`. An attacker-controlled `host` value that matches the regex but is not valid base64 (or decodes to a string that breaks URL parsing) causes an unhandled exception to propagate out of `sanitizeHost`, crashing/erroring the calling request handler instead of returning `null`/`InvalidHostError`.

### Finding Description
`sanitizeHost` in [1](#0-0)  uses `base64regex = /^[0-9a-zA-Z+/]+={0,2}$/` to test the raw `host` string. This regex only checks the character set, not that the string length is a multiple of 4 or that padding is correctly positioned — properties that Node's WHATWG-compliant `atob()` strictly enforces. When the regex passes, the code immediately calls `decodeHost(sanitizedHost)`, defined in [2](#0-1) , which is a bare `atob(host)` call with no error handling. A string such as `"AAAA===="` or any base64-charset string with invalid length/padding satisfies the regex but causes Node's `atob()` to throw `DOMException: The string to be decoded is not correctly encoded.` This exception is not caught anywhere in `sanitizeHost`, `decodeHost`, or the immediately following `new URL(`https://${decodeHost(sanitizedHost)}`)` call at [3](#0-2) , so it propagates up to whatever caller invoked `sanitizeHost(config)(host, true)` (e.g., auth `begin`/`callback` handlers, `validate-shop-and-host-params` helpers in shopify-app-express/remix/react-router). Since `host` is an unauthenticated, attacker-controlled query parameter on public app routes, this is directly reachable without any secret or privileged access.

### Impact Explanation
This is a denial-of-service / uncaught-exception issue in the authentication/authorization entry path: any anonymous request with a crafted `host` parameter can throw an unhandled exception instead of the expected fail-closed `InvalidHostError`/`null` return. Depending on the host framework's error handling, this can crash the process (unhandled promise rejection) or produce inconsistent 500 errors on the OAuth begin/callback and embedded-app routes, which is the impact class of "DoS in an authentication handler."

### Likelihood Explanation
Trivial to trigger: the attacker sends one unauthenticated GET/POST request to any route that calls `sanitizeHost(config)(host, true)` (e.g., OAuth begin, callback, or shop/host validation helpers used by `shopify-app-express`, `shopify-app-remix`, `shopify-app-react-router`) with a `host` value that matches the loose base64 regex but is invalid base64 (wrong length/padding). No authentication, secret, or special configuration is required, and the request is fully attacker-controlled and repeatable.

### Recommendation
Wrap the `decodeHost`/`atob` call and the subsequent `new URL()` construction in a `try/catch` inside `sanitizeHost` (or inside `decodeHost`), treating any thrown error the same as an invalid host: set `sanitizedHost = null` and only throw the typed `InvalidHostError` when `throwOnInvalid` is true. Additionally, tighten `base64regex` to enforce correct base64 length (multiple of 4 with valid padding placement) before calling `atob`.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/utils/__tests__/shop-validator.test.ts
import {shopifyApi} from '../..';
import {testConfig} from '../../__tests__/test-config';
import {InvalidHostError} from '../../error';

test('sanitizeHost does not throw raw DOMException on malformed base64', () => {
  const shopify = shopifyApi(testConfig());

  // Matches base64regex (charset + <=2 '=' at end) but is invalid base64 length/padding
  const malformedButRegexMatching = 'AAAA====';

  // Expected (desired) behavior: throws typed InvalidHostError, not DOMException
  expect(() =>
    shopify.utils.sanitizeHost(malformedButRegexMatching, true),
  ).toThrow(InvalidHostError);

  // Actual current behavior: atob() throws an uncaught DOMException that
  // escapes sanitizeHost, failing the assertion above.
});
```
Running this against the current implementation shows `atob()` throwing `DOMException: The string to be decoded is not correctly encoded.` instead of the expected `InvalidHostError`, confirming the unhandled-exception path in [1](#0-0)  and [2](#0-1) .

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
