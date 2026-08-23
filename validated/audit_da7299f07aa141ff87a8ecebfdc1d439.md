### Title
`sanitizeHost` throws an uncaught `TypeError` (not `InvalidHostError`) on malformed decoded `host`, crashing callers instead of failing closed - ([File: packages/apps/shopify-api/lib/utils/shop-validator.ts])

### Summary
`sanitizeHost` accepts any string that matches the base64 character-set regex, then blindly passes the decoded value into `new URL('https://' + decodeHost(sanitizedHost))` with no try/catch. Because `decodeHost` (`atob`) can decode to arbitrary bytes, an attacker can craft a base64 `host` value whose decoded form is not a legal URL authority (e.g. contains an unmatched `[`, which the WHATWG URL parser interprets as an IPv6 literal delimiter), causing the native `URL` constructor to throw a `TypeError` that propagates out of `sanitizeHost` uncaught.

### Finding Description
`sanitizeHost` in `packages/apps/shopify-api/lib/utils/shop-validator.ts` only validates that `host` is syntactically valid base64 via `base64regex`, then does:
```
const {hostname} = new URL(`https://${decodeHost(sanitizedHost)}`);
``` [1](#0-0) 

`decodeHost` is a trivial `atob(host)` call with no further sanitization [2](#0-1) , so any base64 string decodes successfully to an arbitrary byte sequence. This decoded string is concatenated directly after `https://` and handed to the standard `URL` constructor without any try/catch anywhere in `sanitizeHost`. If the decoded content is something the WHATWG URL parser cannot parse into a valid authority (for example, an unmatched `[` which the parser interprets as beginning of an IPv6 literal, or other malformed host syntax), `new URL()` throws a native `TypeError`, not a `ShopifyErrors.InvalidHostError`. This exception is not caught anywhere in the function and propagates to the caller.

The `host` query parameter is fully attacker-controlled and unauthenticated at this point — it is read straight from the request URL before any HMAC/session validation, e.g. in `validateShopAndHostParams` [3](#0-2) , `redirectToShopifyOrAppRoot` [4](#0-3) , and `redirectToAuth`'s `clientSideRedirect` [5](#0-4) . None of these call sites wrap `sanitizeHost` in a try/catch — they all assume it either returns a string or `null` (or throws only the documented `InvalidHostError` when `throwOnInvalid` is set). An uncaught `TypeError` here will bubble up as an unhandled exception in the request-handling path (e.g., a Remix loader/action, an Express route handler), which — depending on the host framework's error-handling middleware — can 500 the request or, in the worst case (unhandled promise rejection in async contexts without a catch), crash the Node process.

### Impact Explanation
This is a pre-authentication, unauthenticated DoS vector: any anonymous client that can hit the app's embedded entry route (or auth/login routes that read `host` from the query string) can trigger an uncaught exception by supplying a crafted base64 `host` value. Depending on framework wiring this manifests as a 500 error for that request (if a global error handler catches it) or, in code paths where `sanitizeHost`'s exception isn't caught by any framework middleware (e.g. inside `Promise` chains not wrapped by the framework), can crash the server process — matching the "DoS in an authentication handler" impact class referenced in the audit scope.

### Likelihood Explanation
Preconditions are minimal: default configuration, an embedded app, and a single unauthenticated GET request with a crafted `host` query parameter (e.g. base64 of the string `[` or other malformed authority component). No secrets, privileged access, or non-default configuration are required, and the request is fully reproducible/repeatable.

### Recommendation
Wrap the `new URL(...)` call in `sanitizeHost` in a try/catch, treating any parse error the same as a regex mismatch (i.e., set `sanitizedHost = null` and only throw the documented `InvalidHostError` when `throwOnInvalid` is true):
```ts
let sanitizedHost = base64regex.test(host) ? host : null;
if (sanitizedHost) {
  try {
    const {hostname} = new URL(`https://${decodeHost(sanitizedHost)}`);
    // ...existing hostRegex check...
  } catch {
    sanitizedHost = null;
  }
}
```

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/utils/__tests__/shop-validator.test.ts
test('sanitizeHost should not throw an uncaught TypeError for malformed decoded host', () => {
  const shopify = shopifyApi(testConfig());

  // decodes (via atob) to '[' — an unmatched IPv6 literal delimiter that
  // makes `new URL('https://[')` throw a native TypeError.
  const malformedBase64Host = Buffer.from('[').toString('base64');

  expect(() => shopify.utils.sanitizeHost(malformedBase64Host)).not.toThrow(TypeError);
  expect(shopify.utils.sanitizeHost(malformedBase64Host)).toBe(null);
});
```
Expected current (vulnerable) behavior: the test fails because `sanitizeHost` throws an uncaught native `TypeError: Invalid URL` instead of returning `null`. Equivalent HTTP PoC: `GET /?shop=test-shop.myshopify.com&host=WyE=` (base64 of `[!` or similar malformed host fragment) against any embedded-app route that calls `api.utils.sanitizeHost`/`redirectToShopifyOrAppRoot`/`validateShopAndHostParams`.

### Citations

**File:** packages/apps/shopify-api/lib/utils/shop-validator.ts (L52-60)
```typescript
export function sanitizeHost(config: ConfigInterface) {
  return (host: string, throwOnInvalid = false): string | null => {
    const base64regex = /^[0-9a-zA-Z+/]+={0,2}$/;

    let sanitizedHost = base64regex.test(host) ? host : null;
    if (sanitizedHost) {
      const {hostname} = new URL(`https://${decodeHost(sanitizedHost)}`);

      const originsRegex = [
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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/redirect-to-shopify-or-app-root.ts (L13-13)
```typescript
  const host = api.utils.sanitizeHost(url.searchParams.get('host')!)!;
```

**File:** packages/apps/shopify-app-express/src/redirect-to-auth.ts (L37-37)
```typescript
  const host = api.utils.sanitizeHost(req.query.host as string);
```
