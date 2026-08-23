### Title
`sanitizeHost()` throws an uncaught `DOMException` on malformed-length base64 `host` values instead of returning `null` - ([File: packages/apps/shopify-api/lib/utils/shop-validator.ts])

### Summary
`sanitizeHost()`'s `base64regex = /^[0-9a-zA-Z+/]+={0,2}$/` accepts base64-alphabet strings of *any* length, including lengths that are not valid encoded groupings (e.g. `length % 4 === 1`, with 0 padding characters). Such strings pass the regex but make `decodeHost()`'s `atob(host)` call throw a `DOMException`/`InvalidCharacterError` instead of `sanitizeHost` gracefully returning `null`, and none of the call sites wrap this in try/catch.

### Finding Description
`sanitizeHost` in [1](#0-0)  only checks the character set/padding-symbol shape of `host` via `base64regex`, then unconditionally calls `decodeHost(sanitizedHost)` which does `return atob(host)` in [2](#0-1) .

The regex `/^[0-9a-zA-Z+/]+={0,2}$/` does not enforce that the base64 payload length (mod 4, after removing `=` padding) is a valid grouping (0, 2, or 3 remainder characters). A string whose length modulo 4 is exactly 1 — e.g. a 5-character unpadded string like `AAAAA` — passes the regex but is not decodable base64. Node's/the WHATWG `atob` "forgiving-base64" decode algorithm explicitly throws `InvalidCharacterError` (a `DOMException`) for such inputs, per spec.

Because `sanitizeHost` calls `decodeHost` synchronously and unguarded, this exception propagates straight out of `sanitizeHost` rather than being caught and converted to a `null` return.

The two call sites cited in the question have no try/catch around `sanitizeHost`:
- [3](#0-2) 
- [4](#0-3) 

Both are reachable pre-auth, without any session, cookie, or HMAC requirement — an anonymous request with `?shop=<valid-shop>&host=<malformed>` reaches `sanitizeHost` directly.

The existing test suite in [5](#0-4)  only covers non-base64 characters and mismatched domains — it never exercises a regex-passing-but-improperly-grouped base64 string, so this gap was not caught by tests.

### Impact Explanation
This is a Denial-of-Service in a pre-authentication request path: an unauthenticated caller can throw an uncaught exception inside `sanitizeHost`, which is invoked before any shop/host validation completes, in both the Express (`redirectToAuth`) and Remix/React-Router (`validateShopAndHostParams`) admin-authentication entry points. Depending on the calling framework's error handling, this manifests at minimum as an unhandled exception surfacing as a raw 500/crash rather than the intended graceful `InvalidHostError`/redirect-to-login behavior, and in the Express async chain (`clientSideRedirect` invoked synchronously from the async `redirectToAuth`) it can produce an unhandled promise rejection, which under Node's default `unhandledRejection` behavior terminates the process.

### Likelihood Explanation
Requires zero credentials, zero prior state, and default configuration — just a single crafted GET request with `host` set to a base64-alphabet string whose un-padded length is not a multiple of 4 with 0/2/3 remainder (e.g. `%host=AAAAA`). Fully repeatable and trivially scriptable by any unprivileged client.

### Recommendation
Harden `sanitizeHost`/`decodeHost` to never throw on malformed input:
- Wrap the `decodeHost` call in `sanitizeHost` in a try/catch, returning `null` (or throwing `InvalidHostError` only when `throwOnInvalid` is set) on any decode failure, or
- Strengthen `base64regex` to enforce valid base64 grouping (length mod 4 ∈ {0,2,3} after stripping `=`), or use a dedicated base64 validation/decoding utility that never throws.

### Proof of Concept
```javascript
// packages/apps/shopify-api/lib/utils/__tests__/shop-validator.test.ts
import {shopifyApi} from '../..';
import {testConfig} from '../../__tests__/test-config';

test('sanitizeHost does not throw for regex-passing malformed-length base64', () => {
  const shopify = shopifyApi(testConfig());

  // 5 chars, no padding -> length % 4 === 1, passes base64regex but atob() throws
  expect(() => shopify.utils.sanitizeHost('AAAAA')).not.toThrow();
  expect(shopify.utils.sanitizeHost('AAAAA')).toBe(null);
});
```
Running this against the current implementation throws `DOMException [InvalidCharacterError]` (or `RangeError` in some Node builds) instead of returning `null`, demonstrating the crash path reachable via `?host=AAAAA` on any embedded-app entry route or `/auth`.

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts (L21-21)
```typescript
    const host = api.utils.sanitizeHost(url.searchParams.get('host')!);
```

**File:** packages/apps/shopify-app-express/src/redirect-to-auth.ts (L37-37)
```typescript
  const host = api.utils.sanitizeHost(req.query.host as string);
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
