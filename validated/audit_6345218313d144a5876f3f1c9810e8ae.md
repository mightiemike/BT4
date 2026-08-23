### Title
`sanitizeHost` throws an unhandled exception instead of returning `null` for base64-well-formed but `atob`-invalid `host` values - ([File: packages/apps/shopify-api/lib/utils/shop-validator.ts])

### Summary
`sanitizeHost` validates the `host` query parameter with a permissive regex `/^[0-9a-zA-Z+/]+={0,2}$/` before calling `decodeHost`, which delegates to `atob`. Some strings pass the regex (e.g. `"A"`, or any base64 string whose length mod 4 is 1) but are rejected by the WHATWG "forgiving-base64" decode algorithm used by `atob`, causing it to throw an uncaught `DOMException`/`InvalidCharacterError` inside `sanitizeHost` instead of returning `null`.

### Finding Description
`sanitizeHost` in [1](#0-0)  first tests the raw `host` against `base64regex = /^[0-9a-zA-Z+/]+={0,2}$/` and, if it matches, immediately calls `decodeHost(sanitizedHost)` without any surrounding `try/catch`. `decodeHost` is a thin wrapper around `atob`: [2](#0-1) .

The regex only checks the character set and allows 0–2 trailing `=`, but does not enforce that the string length is a multiple of 4 (accounting for padding). `atob`, per the WHATWG forgiving-base64 decode algorithm, throws when the (unpadded) input length modulo 4 equals 1 — e.g. a single character like `"A"` matches the regex but causes `atob` to throw. Because `sanitizeHost` has no `try/catch` around the `decodeHost` call, this exception propagates out of the function unhandled, rather than the function returning `null` as its contract implies for invalid input (as demonstrated by the existing `INVALID_HOSTS` test cases in [3](#0-2) , none of which cover this malformed-length case).

`sanitizeHost` is reachable directly from an unauthenticated HTTP request via the `host` query parameter in the admin authentication flow, e.g. `validateShopAndHostParams` calls `api.utils.sanitizeHost(url.searchParams.get('host')!)` with no try/catch: [4](#0-3) . Similarly, `redirectWithExitIframe` calls `api.utils.sanitizeHost(queryParams.get('host')!)` unguarded: [5](#0-4) . Neither call site nor `sanitizeHost` itself catches the exception thrown by `atob`.

### Impact Explanation
An anonymous attacker can send `GET /auth?shop=...&host=A` (or any host string that passes the permissive base64 regex but has invalid length/padding for `atob`) to trigger an unhandled exception in the authentication path, before any HMAC or session validation occurs. This causes a per-request crash/500 in the auth handler rather than a clean rejection, matching Shopify's DoS-in-an-authentication-handler impact class. It does not lead to forged sessions, token theft, or cross-tenant access — the scope is limited to request-level denial of service in the code path that processes the `host` parameter.

### Likelihood Explanation
The precondition is minimal: an unauthenticated attacker only needs to send a single crafted query parameter to any route that calls `sanitizeHost` (e.g. the app's `authenticate.admin`/auth entry route), which is a default, always-present part of any Shopify embedded app built with this library. No secrets, non-default configuration, or prior authentication are required, making this trivially and repeatably reproducible.

### Recommendation
Wrap the `decodeHost` call in `sanitizeHost` (and/or `decodeHost` itself) in a `try/catch`, treating any decode failure as an invalid host (return `null` / respect `throwOnInvalid` via `InvalidHostError` instead of letting the raw `atob` exception propagate). Additionally, tighten `base64regex` to require a length that is a multiple of 4 (or verify padding correctness) before attempting to decode.

### Proof of Concept
```javascript
// packages/apps/shopify-api/lib/utils/__tests__/shop-validator.test.ts
test('does not throw for base64-shaped but atob-invalid host', () => {
  const shopify = shopifyApi(testConfig());

  // 'A' matches /^[0-9a-zA-Z+/]+={0,2}$/ but atob('A') throws
  // (length mod 4 === 1 is invalid per forgiving-base64 decode)
  expect(() => shopify.utils.sanitizeHost('A')).not.toThrow();
  expect(shopify.utils.sanitizeHost('A')).toBe(null);
});
```
Equivalent HTTP-level PoC: `GET /auth?shop=my-shop.myshopify.com&host=A` against an app's `authenticate.admin` route — expected: a graceful redirect/login response; actual: an unhandled exception thrown from `sanitizeHost`/`decodeHost`.

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts (L21-21)
```typescript
    const host = api.utils.sanitizeHost(url.searchParams.get('host')!);
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/redirect-with-exitiframe.ts (L15-15)
```typescript
  const host = api.utils.sanitizeHost(queryParams.get('host')!);
```
