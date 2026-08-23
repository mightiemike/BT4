### Title
Uncaught `TypeError` in `sanitizeHost` from malformed decoded host crashes auth handlers instead of returning null - ([File: packages/apps/shopify-api/lib/utils/shop-validator.ts])

### Summary
`sanitizeHost` decodes an attacker-controlled base64 `host` query parameter and feeds the raw decoded bytes directly into `new URL(https://${decodeHost(sanitizedHost)})` with no `try/catch`. Because the base64 regex only validates the base64 *character set*, an attacker can craft a payload that decodes to a string containing forbidden host code points (e.g. a space, `<`, `\`, or an unmatched `[`), which makes the WHATWG `URL` constructor throw a `TypeError`. This exception is never caught inside `sanitizeHost`, so it propagates out of the function regardless of the `throwOnInvalid` flag, breaking the function's documented "return null on invalid input" contract.

### Finding Description [1](#0-0)  `sanitizeHost` performs: [2](#0-1)  only a base64-charset regex check before calling `decodeHost` and immediately constructing a `URL`. `decodeHost` is a thin wrapper over `atob`: [3](#0-2) . Neither `decodeHost` nor the subsequent `new URL(...)` call is wrapped in a `try/catch`.

The WHATWG URL parser trims leading/trailing C0-control/space characters and requires a non-empty host for special schemes like `https`; it also rejects "forbidden host code points" (space, `<`, `>`, `"`, `#`, `/`, `:`, `?`, `@`, `[`, `\`, `]`, `^`, `|`) by throwing a `TypeError: Invalid URL`. An attacker can supply, e.g., `host = base64("a<b")` or `host = base64(" ")`. Both strings pass the base64-charset regex, decode successfully via `atob` (no throw there), and are then handed to `new URL("https://a<b")` / `new URL("https:// ")`, which throw synchronously.

This function is reached directly from unauthenticated request handling paths that read the `host` query parameter before any session/HMAC verification occurs, e.g.:
- `packages/apps/shopify-app-remix/.../validate-shop-and-host-params.ts` calls `api.utils.sanitizeHost(url.searchParams.get('host')!)` with only an `if (!host)` guard — an uncaught throw here is never converted to the expected "redirect to login" behavior.
- `packages/apps/shopify-app-express/src/redirect-to-auth.ts` (`clientSideRedirect`) calls `api.utils.sanitizeHost(req.query.host as string)` with only a null-check guard.
- `packages/apps/shopify-app-express/src/middlewares/redirect-to-shopify-or-app-root.ts` calls `api.utils.sanitizeHost(req.query.host as string)!` similarly.

None of these callers wrap the call in `try/catch`, because the function's contract (return `null` unless `throwOnInvalid`, in which case throw `InvalidHostError`) implies it never throws an unexpected exception type. The malformed-URL case violates that contract by throwing a raw `TypeError` unconditionally, before the `throwOnInvalid`/`InvalidHostError` logic is even reached.

### Impact Explanation
This is a low-severity, request-scoped denial-of-service against the auth/host-validation code path: a single crafted `host` parameter causes an unhandled `TypeError` to escape `sanitizeHost` into caller code that does not expect it. Depending on the framework (Express vs. Remix/React Router loaders), this either surfaces as an uncaught exception in a request handler (potential unhandled error / 500 with no graceful redirect, or in the worst case an unhandled synchronous throw not converted into the framework's expected error response) rather than the intended `null`/redirect-to-login behavior. It does not lead to session/HMAC forgery, cross-tenant access, or token theft — it is confined to breaking the input-validation invariant and disrupting the authenticate/login flow for the specific request.

### Likelihood Explanation
Trivially reachable and repeatable: no authentication, secret, or non-default configuration is required. An anonymous client only needs to send a GET request with a `host` query parameter to any `authenticate.admin`/login route with a base64 string that decodes to a value containing a forbidden host code point (e.g. `<`, `\`, or a lone space). Every request with such a payload deterministically triggers the throw.

### Recommendation
Wrap the `decodeHost`/`new URL(...)` block in `sanitizeHost` in a `try/catch`, treating any decode or URL-parse failure as an invalid host (return `null`, or throw `InvalidHostError` only when `throwOnInvalid` is true), consistent with the rest of the function's contract:
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
// packages/apps/shopify-api/lib/utils/__tests__/shop-validator.test.ts
import {shopifyApi} from '../..';
import {testConfig} from '../../__tests__/test-config';

test('sanitizeHost should not throw uncaught TypeError for malformed decoded host', () => {
  const shopify = shopifyApi(testConfig());

  const malformedHost = Buffer.from('a<b').toString('base64'); // decodes to "a<b"

  // EXPECTED (per contract): returns null, never throws
  // ACTUAL: throws TypeError: Invalid URL, uncaught by sanitizeHost
  expect(() => shopify.utils.sanitizeHost(malformedHost)).not.toThrow();
  expect(shopify.utils.sanitizeHost(malformedHost)).toBeNull();
});
```
Running this against the current implementation fails with an unhandled `TypeError: Invalid URL` thrown from `new URL()` inside `sanitizeHost`, rather than returning `null`.

### Citations

**File:** packages/apps/shopify-api/lib/utils/shop-validator.ts (L52-52)
```typescript
export function sanitizeHost(config: ConfigInterface) {
```

**File:** packages/apps/shopify-api/lib/utils/shop-validator.ts (L54-58)
```typescript
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
