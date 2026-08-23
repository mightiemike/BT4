### Title
`sanitizeHost`/`decodeHost` allows an uncaught `atob` exception to crash the auth request handler on malformed base64 `host` values - ([File: packages/apps/shopify-api/lib/auth/decode-host.ts])

### Summary
`sanitizeHost` validates the `host` query parameter with a regex that only checks for a valid base64 character set (`^[0-9a-zA-Z+/]+={0,2}$`) but does not verify well-formed base64 padding/length, then immediately calls `decodeHost(host)` → `atob(host)` before performing its own `throwOnInvalid` handling. A base64-charset string whose length is ≡1 mod 4 (e.g. `"QQQQQ"`) passes the regex but causes `atob` to throw an untyped `DOMException` (`InvalidCharacterError`), which propagates uncaught out of `sanitizeHost` regardless of the `throwOnInvalid` flag.

### Finding Description [1](#0-0) decodes the host via the browser/Node `atob` global with no error handling. It is called from [2](#0-1) `sanitizeHost`, where the regex `^[0-9a-zA-Z+/]+={0,2}$` (line 54) only enforces the character set, not that the encoded length is a multiple of 4 or correctly padded. Per the WHATWG forgiving-base64 decode algorithm that Node's `atob` implements, a base64 string with length mod 4 equal to 1 is always malformed and causes `atob` to throw synchronously, before `sanitizedHost` is even assigned or the `throwOnInvalid` check at line 85-87 is reached.

This means `sanitizeHost(config)(host, false)` — the default, non-throwing usage — can still throw an unexpected, untyped exception instead of returning `null`, breaking the documented "return null on invalid input" contract. This function is reached directly from unauthenticated request paths: `buildEmbeddedAppUrl`/`getEmbeddedAppUrl` in [3](#0-2) , the Express `redirectToShopifyOrAppRoot` middleware in [4](#0-3) , Express `redirectToAuth`'s client-side redirect in [5](#0-4) , and the Remix/React-Router `validateShopAndHostParams` helper used at the start of `authenticate.admin()` in [6](#0-5) . None of these call sites wrap the `sanitizeHost` call in a try/catch — they all expect a `string | null` return value, not an exception.

### Impact Explanation
An attacker-controlled `host` query parameter reaching any of these code paths (all are part of the embedded-app bootstrap / auth redirect flow, reachable by any unauthenticated client) will cause an unhandled exception instead of the expected `InvalidHostError` or `null` result. Depending on the host framework's error handling (Express route without a wrapping try/catch around an async handler, or Remix loader), this can propagate as an unhandled promise rejection, potentially resulting in a 500-level failure or, in the worst case for frameworks without global rejection handlers, a process crash — a DoS of the authentication/embedded-app-bootstrap handler.

### Likelihood Explanation
Trivial to trigger: no authentication or secrets are required. The attacker only needs to send a `host` query parameter that is base64-charset-valid but has invalid length/padding (e.g., `host=QQQQQ`), which is a single crafted string of any length ≡1 mod 4. This is fully reachable at the OAuth `begin`/redirect and embedded-app bootstrap endpoints of any app built with `@shopify/shopify-api`/`shopify-app-express`/`shopify-app-remix`.

### Recommendation
Wrap the `atob` call in `decodeHost` (or in `sanitizeHost` before calling it) in a try/catch, and on failure treat the host as invalid — returning `null` or throwing a typed `ShopifyErrors.InvalidHostError` consistently with the `throwOnInvalid` contract, rather than letting the raw `DOMException` propagate. Additionally, tighten the `base64regex` to enforce valid base64 length (`^(?:[0-9a-zA-Z+/]{4})*(?:[0-9a-zA-Z+/]{2}==|[0-9a-zA-Z+/]{3}=|[0-9a-zA-Z+/]{4})$`) so malformed-length strings are rejected before reaching `atob`.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/utils/__tests__/shop-validator.test.ts
test('sanitizeHost does not throw an uncaught exception for malformed base64 padding', () => {
  const shopify = shopifyApi(testConfig());

  // length 5, mod 4 == 1 -> passes base64regex but is invalid base64
  const malformedHost = 'QQQQQ';

  expect(() => shopify.utils.sanitizeHost(malformedHost)).not.toThrow();
  expect(shopify.utils.sanitizeHost(malformedHost)).toBeNull();

  // Even with throwOnInvalid it should raise the typed error, not a DOMException
  expect(() => shopify.utils.sanitizeHost(malformedHost, true)).toThrow(
    InvalidHostError,
  );
});
```
Expected current (buggy) behavior: `shopify.utils.sanitizeHost('QQQQQ')` throws `DOMException [InvalidCharacterError]` instead of returning `null`, failing the first assertion.

### Citations

**File:** packages/apps/shopify-api/lib/auth/decode-host.ts (L1-3)
```typescript
export function decodeHost(host: string): string {
  return atob(host);
}
```

**File:** packages/apps/shopify-api/lib/utils/shop-validator.ts (L52-58)
```typescript
export function sanitizeHost(config: ConfigInterface) {
  return (host: string, throwOnInvalid = false): string | null => {
    const base64regex = /^[0-9a-zA-Z+/]+={0,2}$/;

    let sanitizedHost = base64regex.test(host) ? host : null;
    if (sanitizedHost) {
      const {hostname} = new URL(`https://${decodeHost(sanitizedHost)}`);
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

**File:** packages/apps/shopify-app-express/src/middlewares/redirect-to-shopify-or-app-root.ts (L22-22)
```typescript
      const host = api.utils.sanitizeHost(req.query.host as string)!;
```

**File:** packages/apps/shopify-app-express/src/redirect-to-auth.ts (L37-42)
```typescript
  const host = api.utils.sanitizeHost(req.query.host as string);
  if (!host) {
    res.status(500);
    res.send('No host provided');
    return;
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
