### Title
`sanitizeHost`'s regex pre-check allows malformed base64 to reach `atob`, causing an uncaught `DOMException`/`InvalidCharacterError` - (File: packages/apps/shopify-api/lib/utils/shop-validator.ts)

### Summary
`sanitizeHost` validates the `host` query parameter against `base64regex = /^[0-9a-zA-Z+/]+={0,2}$/`, which does not enforce that the string length is a multiple of 4, then passes it straight into `decodeHost`, which is a one-line wrapper around the platform `atob`. Malformed-but-regex-valid base64 (e.g., length ≡ 1 mod 4) causes `atob` to throw an uncaught `DOMException`/`InvalidCharacterError` instead of `sanitizeHost` returning `null` or throwing the library's own `InvalidHostError`.

### Finding Description
`sanitizeHost` in [1](#0-0)  only pre-validates the `host` string with a permissive alphabet regex before calling `decodeHost(sanitizedHost)`, and `decodeHost` is a direct pass-through to `atob` with no try/catch: [2](#0-1) . Unlike `sanitizeShop`, which only uses regex matching and never throws unexpectedly, `sanitizeHost`'s only guard against bad decoding is the base64 alphabet regex, which does not check `length % 4`. WHATWG-spec-compliant `atob` implementations throw `InvalidCharacterError` for base64 strings whose length (after padding removal) is not congruent to 0, 2, or 3 mod 4 (i.e., remainder 1), which the regex does not exclude.

This function is reachable from `req.query.host` in multiple entry points without any surrounding try/catch, e.g. `redirectToAuth`'s `clientSideRedirect` in `shopify-app-express` calls `api.utils.sanitizeHost(req.query.host as string)` directly [3](#0-2) , and `redirectToShopifyOrAppRoot` middleware does the same [4](#0-3) . None of these call sites wrap the call in a try/catch, so a thrown `DOMException` propagates as an unhandled exception in the route/middleware.

### Impact Explanation
This is a defect in this library's input-validation logic: `sanitizeHost` is documented/expected to return `null` for invalid input (as demonstrated by its existing "invalid host" test cases returning `null` rather than throwing) but instead can throw an unexpected, uncaught exception type on certain crafted inputs. In frameworks/setups where the calling code is a synchronous Express handler, Express 4's built-in synchronous-throw handling would route it to the error middleware (resulting in a 500 response rather than a full process crash); however, if the call occurs inside an `async` function without an enclosing try/catch or async-error wrapper (which is the case in the two call sites cited above and similar helpers in `shopify-app-remix`/`shopify-app-react-router`), the exception becomes an unhandled promise rejection. Depending on the host application's Node.js configuration (e.g., default `--unhandled-rejections=throw` behavior in modern Node versions), this can crash the Node process, producing a denial of service for all requests, not just the malicious one — a legitimate DoS impact in an authentication-adjacent handler.

### Likelihood Explanation
No privileges are required: any anonymous client can trigger this by sending a single crafted `host` query parameter to any route that calls `sanitizeHost`/`redirectToAuth`/`redirectToShopifyOrAppRoot` (e.g., `GET /auth?host=<malformed-base64>` or `GET /?host=<malformed-base64>`). The attack requires no secrets, no prior session, and is trivially repeatable, making likelihood high once a vulnerable call path (async, unguarded) is confirmed in a given deployment. The exact severity (500 error vs. process crash) depends on Node.js version and how the host app wires its async middleware, which could not be fully confirmed from the code alone.

### Recommendation
Wrap the `atob` call in `decodeHost` (or in `sanitizeHost`) in a try/catch and return `null` (or throw `InvalidHostError` when `throwOnInvalid` is set) on any decode failure, and/or tighten `base64regex` to also enforce `length % 4 !== 1` (e.g., validate `/^(?:[0-9a-zA-Z+/]{4})*(?:[0-9a-zA-Z+/]{2}==|[0-9a-zA-Z+/]{3}=|[0-9a-zA-Z+/]{4})$/`) before calling `decodeHost`.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/utils/__tests__/shop-validator.test.ts
import {shopifyApi} from '../..';
import {testConfig} from '../../__tests__/test-config';

test('sanitizeHost does not throw on malformed base64 (length % 4 == 1)', () => {
  const shopify = shopifyApi(testConfig());

  // 'QUFBQ' has length 5 (5 % 4 == 1), passes base64regex,
  // but is invalid base64 grouping per WHATWG atob spec.
  expect(() => shopify.utils.sanitizeHost('QUFBQ')).not.toThrow();
  expect(shopify.utils.sanitizeHost('QUFBQ')).toBeNull();
});
```
Expected (current buggy) behavior: the call throws `DOMException [InvalidCharacterError]` from `atob` instead of returning `null`, failing the `not.toThrow()` assertion.

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

**File:** packages/apps/shopify-app-express/src/redirect-to-auth.ts (L30-42)
```typescript
function clientSideRedirect(
  api: Shopify,
  config: AppConfigInterface,
  req: Request,
  res: Response,
  shop: string,
): void {
  const host = api.utils.sanitizeHost(req.query.host as string);
  if (!host) {
    res.status(500);
    res.send('No host provided');
    return;
  }
```

**File:** packages/apps/shopify-app-express/src/middlewares/redirect-to-shopify-or-app-root.ts (L11-22)
```typescript
  return function () {
    return async function (req: Request, res: Response) {
      if (res.headersSent) {
        config.logger.info(
          'Response headers have already been sent, skipping redirection to host',
          {shop: res.locals.shopify?.session?.shop},
        );

        return;
      }

      const host = api.utils.sanitizeHost(req.query.host as string)!;
```
