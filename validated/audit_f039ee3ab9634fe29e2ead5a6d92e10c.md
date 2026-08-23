### Title
`sanitizeHost` throws an unhandled `TypeError` instead of returning `null` for base64 input that decodes to a hostname with forbidden URL code points - ([File: packages/apps/shopify-api/lib/utils/shop-validator.ts])

### Summary
`sanitizeHost` validates the `host` query parameter with a base64-charset regex, then unconditionally calls `new URL(`https://${decodeHost(sanitizedHost)}`)` without a try/catch. `decodeHost` is just `atob(host)`, so any string matching `/^[0-9a-zA-Z+/]+={0,2}$/` will decode successfully, but the decoded bytes are attacker-controlled and can contain forbidden host code points (space, `\u0000`, `#`, `%`, etc.). When that happens, `new URL()` throws a `TypeError`, which propagates out of `sanitizeHost` uncaught, regardless of the `throwOnInvalid` flag.

### Finding Description
`sanitizeHost` in [1](#0-0)  does:
```
let sanitizedHost = base64regex.test(host) ? host : null;
if (sanitizedHost) {
  const {hostname} = new URL(`https://${decodeHost(sanitizedHost)}`);
  ...
``` [2](#0-1) 

`decodeHost` is a trivial wrapper around `atob`: [3](#0-2) . The `base64regex` only checks the charset/padding shape of the input, not the semantics of the decoded content. Any base64-shaped string decodes without error via `atob`, but the decoded bytes are fully attacker-controlled and can include WHATWG "forbidden host code points" (U+0000, U+0009, U+000A, U+000D, U+0020, `#`, `%`, `/`, `:`, `?`, `@`, `[`, `\`, `]`). When such a string is interpolated into `https://${decoded}` and passed to `new URL()`, the URL parser throws a `TypeError` for the malformed authority/host component. That throw happens **before** the `throwOnInvalid`/return-null logic at the bottom of the function is reached, so `sanitizeHost` throws unconditionally in this situation — a caller that expects `sanitizeHost(host)` (with `throwOnInvalid` left at its default `false`) to simply return `null` on invalid input instead crashes with an unhandled `TypeError`.

This function is called directly from `host` query-parameter validation in framework-integration helpers such as `validateShopAndHostParams` in shopify-app-remix ( [4](#0-3) ) and shopify-app-react-router ( [5](#0-4) ), both of which call `api.utils.sanitizeHost(...)` with no try/catch around the call and expect a `null` return to trigger a redirect/App Bridge render path, not a thrown exception.

I was not able to fully confirm, within the remaining tool budget, whether some outer middleware/error boundary in shopify-app-remix or shopify-app-react-router globally catches all thrown `Error`/`TypeError` instances from route loaders (as opposed to only `Response`/`redirect` throws) and converts them into a generic 500 rather than crashing the process; this would determine whether the ultimate effect is "clean 500" vs. an actual unhandled promise rejection/crash. Regardless of that boundary's existence, the request-level defect is real: `sanitizeHost` does not gracefully fail validation as its contract/tests (`shop-validator.test.ts`) imply — it returns `null` for other classes of invalid input, but throws for this one.

### Impact Explanation
An anonymous, unauthenticated request to any embedded-app route that calls `authenticate.admin()` (or otherwise calls `sanitizeHost` with default config, `throwOnInvalid=false`) with a crafted `?host=` value can throw an unhandled `TypeError` inside the authentication/validation code path instead of the expected graceful `null`/redirect handling. This is a denial-of-service / exception-handling defect in an authentication-adjacent handler, matching the "DoS in an authentication handler" impact class in scope. It does not itself lead to auth bypass, token theft, or cross-tenant access — the impact is limited to unhandled exceptions / potential 500s on the affected route.

### Likelihood Explanation
Trivially reproducible by any anonymous client: no privileged role, no secret, and no non-default configuration is required — just crafting a base64-charset string (satisfying `base64regex`) whose decoded bytes contain a forbidden host character (e.g., a space or `\u0000`). This is fully attacker-controlled and repeatable on every request.

### Recommendation
Wrap the `new URL(...)` construction inside `sanitizeHost` in a try/catch, treating any decoding/parsing failure the same as a regex mismatch (set `sanitizedHost = null` and only throw `InvalidHostError` if `throwOnInvalid` is set), for example:
```ts
let sanitizedHost = base64regex.test(host) ? host : null;
if (sanitizedHost) {
  try {
    const {hostname} = new URL(`https://${decodeHost(sanitizedHost)}`);
    // ... existing hostRegex checks
  } catch {
    sanitizedHost = null;
  }
}
```

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/utils/__tests__/shop-validator.test.ts (added case)
import {shopifyApi} from '../..';
import {testConfig} from '../../__tests__/test-config';

test('does not throw for base64 that decodes to a malformed host', () => {
  const shopify = shopifyApi(testConfig());

  // "not a url \u0000 host" -> base64 (valid base64 charset, passes base64regex)
  const malicious = Buffer.from('not a url \u0000 host').toString('base64');

  // Expected: should return null gracefully
  // Actual (current code): throws TypeError from `new URL()`
  expect(() => shopify.utils.sanitizeHost(malicious)).not.toThrow();
  expect(shopify.utils.sanitizeHost(malicious)).toBeNull();
});
```
Running this against the current implementation throws `TypeError: Invalid URL` from within `sanitizeHost` rather than returning `null`, confirming the defect. An equivalent HTTP PoC is a `GET /auth/...?shop=<valid>&host=<malicious base64>` request to an app using `shopify-app-remix`/`shopify-app-react-router`'s `authenticate.admin()`, which calls `sanitizeHost` via `validateShopAndHostParams` without a surrounding try/catch.

### Citations

**File:** packages/apps/shopify-api/lib/utils/shop-validator.ts (L52-90)
```typescript
export function sanitizeHost(config: ConfigInterface) {
  return (host: string, throwOnInvalid = false): string | null => {
    const base64regex = /^[0-9a-zA-Z+/]+={0,2}$/;

    let sanitizedHost = base64regex.test(host) ? host : null;
    if (sanitizedHost) {
      const {hostname} = new URL(`https://${decodeHost(sanitizedHost)}`);

      const originsRegex = [
        'myshopify\\.com',
        'shopify\\.com',
        'myshopify\\.io',
        'spin\\.dev',
        'shop\\.dev',
      ];

      if (config.domainTransformations) {
        const hostTransformationDomains = config.domainTransformations
          .filter((t) => t.includeHost !== false)
          .flatMap((t) =>
            getTransformationDomains({
              ...config,
              domainTransformations: [t],
            }),
          );
        originsRegex.push(...hostTransformationDomains);
      }

      const hostRegex = new RegExp(`\\.(${originsRegex.join('|')})$`);
      if (!hostRegex.test(hostname)) {
        sanitizedHost = null;
      }
    }
    if (!sanitizedHost && throwOnInvalid) {
      throw new InvalidHostError('Received invalid host argument');
    }

    return sanitizedHost;
  };
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

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts (L21-21)
```typescript
    const host = api.utils.sanitizeHost(url.searchParams.get('host')!);
```
