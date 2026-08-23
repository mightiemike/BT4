### Title
Uncaught `SafeCompareError` DoS via array-typed `hmac` query parameter in `validateHmac` (admin signator) - ([File: packages/apps/shopify-api/lib/utils/hmac-validator.ts])

### Summary
`shopify.utils.validateHmac()` and the OAuth flow that relies on it assume that HMAC-related query values are always plain strings. For the `admin` signator, `normalizeQuery()` only guards against array-valued security parameters when the query is a `URLSearchParams` instance (or when the signator is `appProxy`); a plain object query (e.g. Express's `req.query` with duplicate keys) can carry an array-typed `hmac` value straight through to `safeCompare()`, which throws an uncaught `SafeCompareError` instead of returning `false`.

### Finding Description
`validateHmac()` normalizes the incoming query via `normalizeQuery()`: [1](#0-0) 

For plain-object queries (not `URLSearchParams`), array-valued security parameters (`hmac`, `shop`, `signature`, `timestamp`) are only rejected when `signator === 'appProxy'`. For the default `admin` signator, the object is returned unchanged, so `hmac` can remain an array.

`validateHmac()` then only special-cases `timestamp` for array detection via `validateHmacTimestamp()`: [2](#0-1) 

but performs no equivalent check on `hmac` itself before calling: [3](#0-2) 

`safeCompare()` requires both operands to have the same `typeof`; on mismatch it throws `SafeCompareError` rather than returning `false`: [4](#0-3) 

`shopify.utils.validateHmac` is a documented, public API intended to be called directly with `req.query` from a webserver framework (e.g. Express), which by default parses duplicate query-string keys into arrays: [5](#0-4) 

The documented signature only advertises a `boolean` return value with no mention of a thrown exception: [6](#0-5) 

This mirrors the original fee-on-transfer bug class: code assumes an external input always has an expected exact "shape" (a plain amount / a plain string), and when an attacker perturbs that shape (fee deduction / duplicated query parameter → array), a hard failure (`require` revert / uncaught `SafeCompareError`) occurs instead of the intended graceful rejection, causing a denial of service in the code path that is supposed to just validate and reject bad input.

### Impact Explanation
Any caller that follows the documented usage pattern (`await shopify.utils.validateHmac(req.query)`) without wrapping it in a try/catch will have an unhandled promise rejection thrown from a single, unauthenticated HTTP request that duplicates the `hmac` query parameter (e.g. `?hmac=a&hmac=b&shop=...&state=...&timestamp=<valid>`). Because Express's default query parser (`qs`) turns duplicate keys into arrays, this is trivially reachable from any anonymous request to an OAuth callback / HMAC-validated route built on `shopify-app-express` or any custom route using this documented utility. Depending on the app's global error handling, this can crash the request (500) or, in worse setups, the Node process — a DoS of the authentication handler.

### Likelihood Explanation
Medium. It requires: (1) a caller using the public `validateHmac` API directly on a plain-object query (matches the officially documented usage), and (2) a webserver/query parser that turns repeated query keys into arrays (Express default). The bundled `lib/auth/oauth/oauth.ts` `callback()` flow itself is not affected because it parses the URL with `URLSearchParams` + `Object.fromEntries()`, which collapses duplicates to the last value rather than an array — so the built-in OAuth flow is safe, but the documented/public `validateHmac()` utility used directly by app authors is not.

### Recommendation
In `normalizeQuery()`, apply the same single-value enforcement performed for `appProxy` to the `admin` signator as well (reject array-valued `hmac`/`shop`/`timestamp` for plain-object queries regardless of signator). Additionally, have `validateHmac()` catch/normalize `SafeCompareError` (or validate operand types before calling `safeCompare`) so malformed input always results in a returned `false`/thrown `InvalidHmacError`, never an unexpected uncaught error type.

### Proof of Concept
1. Build an Express app using `shopify-app-express`/`shopify-api` and a custom route that calls `await shopify.utils.validateHmac(req.query)` as shown in the official docs, without a try/catch (matching documented usage).
2. Send: `GET /custom-route?code=abc&shop=my-shop.myshopify.com&state=nonce&timestamp=<currentUnixTime>&hmac=aaa&hmac=bbb`
3. Express's default `qs` parser sets `req.query.hmac = ['aaa', 'bbb']`.
4. `validateHmac()` → `normalizeQuery()` returns the object unchanged (admin signator, plain object) → `validateHmacTimestamp` passes (timestamp is a valid single string) → `safeCompare(['aaa','bbb'], localHmacString)` → `typeof` mismatch (`object` vs `string`) → throws `SafeCompareError`, propagating out of `validateHmac()` as an uncaught rejection in the calling route.

### Citations

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L105-115)
```typescript
    const hmac =
      signator === 'appProxy'
        ? normalizedQuery.signature
        : normalizedQuery.hmac;
    const localHmac = await generateLocalHmac(config)(
      normalizedQuery,
      signator,
    );

    return safeCompare(hmac as string, localHmac);
  };
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

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L203-222)
```typescript
function validateHmacTimestamp(query: AuthQuery) {
  const {timestamp} = query;

  if (
    timestamp === undefined ||
    timestamp === null ||
    Array.isArray(timestamp)
  ) {
    throw new ShopifyErrors.InvalidHmacError(
      'HMAC timestamp is missing or invalid',
    );
  }

  const parsedTimestamp = Number(timestamp);

  if (!Number.isInteger(parsedTimestamp)) {
    throw new ShopifyErrors.InvalidHmacError(
      'HMAC timestamp is missing or invalid',
    );
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

**File:** packages/apps/shopify-api/docs/reference/utils/validateHmac.md (L1-19)
```markdown
# shopify.utils.validateHmac

Shopify requests include an `hmac` query argument (or, in the case of app proxy requests, a `signature` query argument). This method validates those requests to ensure that the `hmac` value was signed by Shopify and not spoofed.

## Example

For OAuth requests:

```ts
const isValid = await shopify.utils.validateHmac(req.query);
```

For App Proxy requests:

```ts
const isValid = await shopify.utils.validateHmac(req.query, {
  signator: 'appProxy',
});
```
```

**File:** packages/apps/shopify-api/docs/reference/utils/validateHmac.md (L21-34)
```markdown
## Parameters

### query

`{[key: string]: any}` | :exclamation: required

The request query arguments.

## Return

`boolean`

Whether the `hmac`/`signature` value in the query is valid.

```
