### Title
Duplicate `hmac` query parameter for `admin` signator crashes `validateHmac` with uncaught `SafeCompareError` - ([File: packages/apps/shopify-api/lib/utils/hmac-validator.ts])

### Finding Description
`normalizeQuery` in `packages/apps/shopify-api/lib/utils/hmac-validator.ts` only enforces the single-value restriction (`APP_PROXY_SINGLE_VALUE_PARAMS`) when `signator === 'appProxy'`, both in the `URLSearchParams` branch and the plain-object branch: [1](#0-0) 

For `signator === 'admin'`, if the caller passes a plain `AuthQuery` object (not a `URLSearchParams` instance) where `hmac` has already been parsed into an array (e.g. by an Express-style query parser that turns repeated `hmac` params into `string[]`), `normalizeQuery` returns the object unmodified at line 130, with `hmac` still an array.

Downstream, `validateHmac` extracts `hmac = normalizedQuery.hmac` (an array) and compares it against `localHmac` (a string) via `safeCompare`: [2](#0-1) 

`safeCompare` only handles the case where both arguments have the same `typeof`; when they differ (here `'object'` for the array vs `'string'` for the hex digest) it throws `SafeCompareError` instead of returning `false`: [3](#0-2) 

This is a genuine asymmetry in the code: the `appProxy` path was explicitly hardened against duplicate single-value parameters, but the `admin` path was not, despite both sharing the same `normalizeQuery`/`safeCompare` machinery.

However, `validateHmac`'s declared return type is `Promise<boolean>`, but the function already throws `ShopifyErrors.InvalidHmacError` under multiple ordinary invalid-input conditions (missing `hmac`, missing/invalid `timestamp`, expired timestamp) at lines 91-101 and inside `validateHmacTimestamp`. Any caller of this exported utility must already be prepared to catch thrown errors from this function as part of normal, expected behavior — it is not a boolean-only contract in practice. The additional `SafeCompareError` thrown for a duplicated `hmac` parameter is therefore an instance of the same general failure mode (an unexpected error class) that callers integrating this library must already handle, rather than a wholly new class of uncaught exception. I could not verify, within the available context, whether any OAuth callback handler in this repo (e.g., in `shopify-app-express`, `shopify-app-remix`, `shopify-app-react-router`) narrowly catches only `InvalidHmacError` while leaving other thrown errors (like `SafeCompareError`) unhandled — that would be required to confirm a concrete DoS distinct from the already-expected error-throwing behavior of `validateHmac`.

### Impact Explanation
If reachable, the practical impact is limited to an unhandled exception in a single request-handling code path (a single failed OAuth callback request), not persistent denial of service, cross-tenant compromise, or authentication bypass — `safeCompare` still fails closed (throwing, not returning `true`). This would at most match a low-severity "input validation / error handling" issue rather than a full auth-handler DoS, since the function's `InvalidHmacError` throws already require equivalent error handling by any caller.

### Likelihood Explanation
Exploitability strictly requires that some host framework/integration calls `validateHmac(config)(query, {signator: 'admin'})` with a raw, non-normalized query object (not `URLSearchParams`) where `hmac` can arrive as an array, and that its error handling does not already generically catch errors thrown by this function. I was unable to confirm, with the tool budget available, whether the in-repo OAuth callback implementations (`lib/auth/oauth/oauth.ts` and the framework packages) pass such an object for the `admin` signator, or whether their surrounding try/catch already covers this case generically alongside the pre-existing `InvalidHmacError` throws.

### Recommendation
Apply the same single-value enforcement in `normalizeQuery` regardless of `signator` (or at minimum also for `admin`'s `hmac`/`timestamp`/`shop` parameters), and/or make `safeCompare`/`validateHmac` coerce or reject non-string `hmac` values with a typed `InvalidHmacError` before reaching `safeCompare`, so behavior is consistent and documented for all callers.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/utils/__tests__/hmac-validator.test.ts
import {validateHmac} from '../hmac-validator';

test('duplicate hmac param for admin signator throws SafeCompareError', async () => {
  const query = {
    code: 'abc',
    hmac: ['a', 'b'], // simulates Express req.query duplicate-param parsing
    shop: 'x.myshopify.com',
    timestamp: `${Math.trunc(Date.now() / 1000)}`,
  };

  await expect(
    validateHmac(config)(query as any, {signator: 'admin'}),
  ).rejects.toThrow(); // throws SafeCompareError instead of returning false
});
```

I was unable to fully confirm whether any host OAuth callback in this repository forwards such a raw, unparsed query object with an unhandled `SafeCompareError` outside of existing generic error handling for `InvalidHmacError`; this could not be verified within the tool-call budget available.

### Citations

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L105-114)
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
