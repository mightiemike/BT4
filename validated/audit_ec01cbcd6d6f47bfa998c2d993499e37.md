## Vulnerability: Insufficient base64 length validation in `sanitizeHost` leads to unhandled exception in auth/redirect handlers - ([File: packages/apps/shopify-api/lib/utils/shop-validator.ts])

### Summary
`sanitizeHost` validates the `host` query parameter using a permissive regex that only checks the base64 *character set*, not that the string has a decodable base64 *length*. Malformed-but-regex-matching values (e.g. `host=A`, length ≡ 1 mod 4) pass validation and are later handed to `decodeHost`, which calls the WHATWG `atob()` primitive. `atob()` throws an uncaught exception for such inputs, and several unauthenticated call sites do not catch it.

### Finding Description
`sanitizeHost` only checks the shape of the input, not whether it is actually decodable: [1](#0-0) 

```
const base64regex = /^[0-9a-zA-Z+/]+={0,2}$/;
let sanitizedHost = base64regex.test(host) ? host : null;
if (sanitizedHost) {
  const {hostname} = new URL(`https://${decodeHost(sanitizedHost)}`);
  ...
```

This regex accepts any string of base64-alphabet characters with 0–2 trailing `=`, including strings whose length modulo 4 equals 1 (e.g. a single character `A`, or 5, 9, … characters). Per the base64 decoding algorithm used by `atob`, such lengths are **not decodable** and must throw. `decodeHost` performs no error handling: [2](#0-1) 

Because the “clamp” (true base64 length/format validation) is missing before the value is fed into `atob`, this is structurally the same bug class as the Vyper report: input that superficially looks well-formed is not actually validated against the real constraints of the consumer, causing the consumer to fault on out-of-range/malformed input.

`sanitizeHost(host, true)` and the un-guarded `sanitizeHost(...)!` pattern are used directly in reachable, unauthenticated auth/redirect code paths, none of which wrap the call in a try/catch:

- `buildEmbeddedAppUrl` calls `sanitizeHost(config)(host, true)` immediately before `decodeHost(host)`, with no try/catch around either call: [3](#0-2) 
- Express `redirectToShopifyOrAppRoot` middleware calls `api.utils.sanitizeHost(req.query.host as string)!` unguarded: [4](#0-3) 
- Express `redirectToAuth`’s `clientSideRedirect` calls `api.utils.sanitizeHost(req.query.host as string)` directly on an embedded-redirect request: [5](#0-4) 
- The React Router/Remix equivalents call `api.utils.sanitizeHost(url.searchParams.get('host')!)!` unguarded as well: [6](#0-5) , and in the shop/host param validator used at the start of `authenticate.admin`: [7](#0-6) 

### Impact Explanation
An anonymous request that reaches any of these code paths (e.g. an OAuth/embedded-app redirect endpoint, `authenticate.admin` entry, or the express `redirectToShopifyOrAppRoot`/`redirectToAuth` middlewares) with a crafted `host` parameter whose length is not a valid base64 length (but still matches the character-class regex) will cause `atob()` to throw. Because none of these call sites catches the exception, the request handler faults with an unhandled exception instead of a controlled 4xx response. In frameworks/deployments where this isn't automatically converted into a safe per-request error (e.g. bare Express middleware not wrapped in async-error handling), this can crash or destabilize the request-handling process — a denial-of-service of the app's OAuth/auth entry points, triggerable by any anonymous actor with no prior authentication.

### Likelihood Explanation
High: the attack requires only a single crafted query parameter (`host=A` or any base64-alphabet string with length % 4 == 1) sent to a public, unauthenticated endpoint (embedded app entry / OAuth begin-redirect / callback root-redirect). No secrets, session, or prior interaction are required.

### Recommendation
Harden `sanitizeHost` (and/or `decodeHost`) to reject any string whose length is not a valid, fully-decodable base64 length (i.e., validate `atob` succeeds, wrapping it in try/catch and returning `null`/throwing `InvalidHostError` on failure) instead of relying solely on the character-class regex. Additionally, wrap all call sites that invoke `sanitizeHost`/`decodeHost` on user-controlled input in error handling so a decode failure degrades to a controlled 400/401 response rather than an unhandled exception.

### Proof of Concept
1. Send a request to any embedded-app entry point or redirect handler that calls `sanitizeHost`, e.g.:
   `GET /auth?shop=my-shop.myshopify.com&host=A`
2. `host=A` matches `^[0-9a-zA-Z+/]+={0,2}$` (single alphanumeric char, no padding needed by the regex) and is returned as "sanitized" by `sanitizeHost`.
3. Downstream code (e.g. `buildEmbeddedAppUrl`, or `redirectToShopifyOrAppRoot`) calls `decodeHost('A')` → `atob('A')`, which throws because a base64 string of length 1 is not decodable.
4. No caller in the cited paths catches this exception, so the request fails with an unhandled error instead of the expected `InvalidHostError`/400 response.

Note: I was not able to execute this against a live runtime to confirm the exact exception type/message from Node's `atob` in this repository's target runtimes (Node/Workers/Deno adapters may differ slightly in error behavior), so this should be verified experimentally before remediation is prioritized.

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/redirect-to-shopify-or-app-root.ts (L13-13)
```typescript
  const host = api.utils.sanitizeHost(url.searchParams.get('host')!)!;
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts (L21-21)
```typescript
    const host = api.utils.sanitizeHost(url.searchParams.get('host')!);
```
