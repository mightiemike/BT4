### Title
Array-valued `hmac`/`shop` query params bypass admin-signator validation and cause an uncaught `SafeCompareError` in `validateHmac` - ([File: packages/apps/shopify-api/lib/utils/hmac-validator.ts])

### Summary
`validateHmac`'s `normalizeQuery` only rejects array-valued security parameters (`hmac`, `shop`, `signature`, `timestamp`) when `signator === 'appProxy'`; for the default `'admin'` signator, a plain-object query (as returned by Express's default `qs`-based query parser and as shown in the library's own docs, e.g. `shopify.utils.validateHmac(req.query)`) is passed through unchanged. If an attacker sends duplicate `hmac` (or `shop`) query parameters, `normalizedQuery.hmac` becomes an array, which is later compared against the freshly computed string HMAC in `safeCompare`, causing an uncaught `SafeCompareError` due to the `typeof` mismatch instead of a controlled `false`/`InvalidHmacError`.

### Finding Description
`normalizeQuery` (packages/apps/shopify-api/lib/utils/hmac-validator.ts lines 118-131) only performs the array-guard (`APP_PROXY_SINGLE_VALUE_PARAMS`) when `signator === 'appProxy'`. For `signator === 'admin'` (the default, per line 87), a non-`URLSearchParams` query object is returned as-is, arrays included: [1](#0-0) .

The `!normalizedQuery.hmac` truthiness check (line 91) does not reject arrays, and `validateHmacTimestamp` only special-cases the `timestamp` field, not `hmac`: [2](#0-1) . `generateLocalHmac` strips `hmac`/`signature` from the query before stringifying, so `localHmac` is always a plain string: [3](#0-2) . When `hmac` is an array, `safeCompare(hmac as string, localHmac)` receives mismatched types (`object` vs `string`) and throws `SafeCompareError` rather than returning `false`: [4](#0-3) .

This is directly reachable because the library's own public documentation instructs developers to call `validateHmac` with the raw Express-style query object for admin/OAuth flows: `const isValid = await shopify.utils.validateHmac(req.query);` — no `URLSearchParams` conversion, no `signator` override (defaults to `'admin'`). Express's default `qs` query parser turns duplicate query keys (`?hmac=a&hmac=b`) into an array automatically, so this is triggerable with a plain unauthenticated GET request using the officially documented API usage — not a host-app bug or non-default configuration.

By contrast, the library's own internal `oauth.ts` callback always converts the URL to `URLSearchParams` and then `Object.fromEntries(query.entries())` before calling `validateHmac`, which collapses duplicate keys to a single string, so the internal OAuth callback route is not affected by this exact vector — the affected surface is host apps that follow the documented direct-object usage.

### Impact Explanation
This causes an unhandled/uncaught exception (`SafeCompareError`) instead of the expected `false`/`InvalidHmacError` fail-closed behavior in an authenticity-checking function, per the docs' encouraged call pattern `shopify.utils.validateHmac(req.query)`. This matches a DoS-class impact in an authentication/HMAC-validation code path — the request handler processing this call can crash or return an unhandled 500 error if the calling code does not explicitly catch generic errors (many callers only catch `ShopifyErrors.InvalidHmacError` per the API's designed error contract).

### Likelihood Explanation
Trivial and repeatable: any anonymous client can send a GET request with a duplicated `hmac` query parameter to any route that follows the documented pattern `validateHmac(req.query)` with default `signator: 'admin'`. No secrets, no prior authentication, and no non-default configuration are required — only that the host uses Express's default query parsing (or any parser that turns duplicate keys into arrays), which is the default/idiomatic usage explicitly shown in the library's own docs.

### Recommendation
Apply the same array-guard used for `appProxy` to the `admin` signator in `normalizeQuery` (packages/apps/shopify-api/lib/utils/hmac-validator.ts lines 118-131) — i.e., always reject array-valued `hmac`/`shop`/`timestamp` regardless of signator, or explicitly cast/validate `hmac`/`signature` are non-array strings before calling `safeCompare`, throwing a controlled `InvalidHmacError` instead of letting `safeCompare` throw an unrelated `SafeCompareError`.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/utils/__tests__/hmac-validator.test.ts
import {shopifyApi} from '../..';
import {testConfig} from '../../__tests__/test-config';

test('admin signator: array-valued hmac throws uncaught SafeCompareError instead of failing closed', async () => {
  const shopify = shopifyApi(testConfig({apiSecretKey: 'my super secret key'}));

  const query = {
    code: 'abc',
    shop: 'x.myshopify.com',
    timestamp: String(Math.trunc(Date.now() / 1000) - 30),
    hmac: ['a', 'b'], // simulates Express/qs parsing `?hmac=a&hmac=b`
  };

  // Expected (bug): rejects with SafeCompareError (uncaught type mismatch),
  // not a clean `false` or InvalidHmacError as other reason-string cases do.
  await expect(
    shopify.utils.validateHmac(query as any, {signator: 'admin'}),
  ).rejects.toThrow(/Mismatched data types provided/);
});
```
Equivalent HTTP request against any host route implementing `const isValid = await shopify.utils.validateHmac(req.query);` (as documented):
```
GET /auth/callback?code=x&shop=x.myshopify.com&timestamp=<now>&hmac=a&hmac=b HTTP/1.1
```

### Citations

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L68-82)
```typescript
export function generateLocalHmac(config: ConfigInterface) {
  return async (
    params: AuthQuery,
    signator: HMACSignator = 'admin',
  ): Promise<string> => {
    const {hmac: _hmac, signature: _signature, ...query} = params;

    const queryString =
      signator === 'admin'
        ? stringifyQueryForAdmin(query)
        : stringifyQueryForAppProxy(query);

    return createSHA256HMAC(config.apiSecretKey, queryString, HashFormat.Hex);
  };
}
```

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L84-114)
```typescript
export function validateHmac(config: ConfigInterface) {
  return async (
    query: HmacQuery,
    {signator}: {signator: HMACSignator} = {signator: 'admin'},
  ): Promise<boolean> => {
    const normalizedQuery = normalizeQuery(query, signator);

    if (signator === 'admin' && !normalizedQuery.hmac) {
      throw new ShopifyErrors.InvalidHmacError(
        'Query does not contain an HMAC value.',
      );
    }

    if (signator === 'appProxy' && !normalizedQuery.signature) {
      throw new ShopifyErrors.InvalidHmacError(
        'Query does not contain a signature value.',
      );
    }

    validateHmacTimestamp(normalizedQuery);

    const hmac =
      signator === 'appProxy'
        ? normalizedQuery.signature
        : normalizedQuery.hmac;
    const localHmac = await generateLocalHmac(config)(
      normalizedQuery,
      signator,
    );

    return safeCompare(hmac as string, localHmac);
```

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L118-131)
```typescript
function normalizeQuery(query: HmacQuery, signator: HMACSignator): AuthQuery {
  if (!(query instanceof URLSearchParams)) {
    if (signator === 'appProxy') {
      for (const key of APP_PROXY_SINGLE_VALUE_PARAMS) {
        if (Array.isArray(query[key])) {
          throw new ShopifyErrors.InvalidHmacError(
            `Query parameter "${key}" must not appear more than once.`,
          );
        }
      }
    }

    return query;
  }
```

**File:** packages/apps/shopify-api/lib/auth/oauth/safe-compare.ts (L8-23)
```typescript
export const safeCompare: SafeCompare = (strA, strB) => {
  if (typeof strA === typeof strB) {
    const enc = new TextEncoder();
    const buffA = enc.encode(JSON.stringify(strA));
    const buffB = enc.encode(JSON.stringify(strB));

    if (buffA.length === buffB.length) {
      return timingSafeEqual(buffA, buffB);
    }
  } else {
    throw new ShopifyErrors.SafeCompareError(
      `Mismatched data types provided: ${typeof strA} and ${typeof strB}`,
    );
  }
  return false;
};
```
