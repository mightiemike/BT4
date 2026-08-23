### Title
Admin-signator HMAC validation does not reject array-valued `hmac`/`shop` security parameters, unlike App Proxy - ([File: packages/apps/shopify-api/lib/utils/hmac-validator.ts])

### Summary
`validateHmac()`'s `normalizeQuery()` helper only guards against duplicated/array-valued security parameters (`hmac`, `shop`, `signature`, `timestamp`) when `signator === 'appProxy'`. For `signator === 'admin'` (the default), a plain-object query with an array-valued `hmac` or `shop` is passed through unchecked, so the deviation-style security check (`!normalizedQuery.hmac`) and the timestamp check pass, and only the `timestamp` field is explicitly checked for `Array.isArray()`. This is the same bug class as the external report: one code path (App Proxy) was hardened to keep a security invariant consistent, while the sibling path (Admin) was left with a stale/incomplete check, producing an inconsistent, exploitable validation result.

### Finding Description
`normalizeQuery()` in `packages/apps/shopify-api/lib/utils/hmac-validator.ts` explicitly rejects array-valued `hmac`, `shop`, `signature`, and `timestamp` only under the `appProxy` branch: [1](#0-0) 

For the `admin` signator with a plain (non-`URLSearchParams`) query object, the function returns the query unchanged with no array check at all. Downstream, `validateHmac()` only rejects a falsy `hmac` and delegates timestamp-array rejection to `validateHmacTimestamp`, which explicitly checks only the `timestamp` field: [2](#0-1) [3](#0-2) 

This mirrors the external report's root cause pattern: a shared validation routine has two code paths that must maintain the same invariant (rejecting duplicated/array security parameters), but the invariant was only enforced on one branch (`appProxy`), leaving the other (`admin`) stale and out of sync — exactly as `totalValues` was kept in sync for `_validateExternalTrades()` but not for `_processInternalTrades()`, producing an inconsistent state for the subsequent security check.

`shopify.utils.validateHmac` is a documented, exported public API intended to be called directly by app authors with request query data (not necessarily pre-normalized via `URLSearchParams`). Framework query parsers such as Express's default `qs` parser turn repeated query keys (e.g., `?hmac=a&hmac=b`) into arrays automatically, so a caller who passes `req.query` straight into `validateHmac(query, {signator: 'admin'})` — the default signator — receives no protection against a duplicated/array-valued `hmac` or `shop` parameter, even though the equivalent `appProxy` path was hardened against exactly this in commit `857c598`.

### Impact Explanation
If an app author invokes `shopify.utils.validateHmac()` directly (as documented) with a raw, unnormalized query (e.g., `req.query` from Express, or any other multi-value query representation), an attacker who can submit duplicate `hmac` or `shop` query parameters may be able to smuggle a second/conflicting value through the admin-signator code path that the appProxy path explicitly disallows. This breaks the parameter-integrity guarantee the library establishes elsewhere (and documents as a hardening fix for appProxy), and can be leveraged to cause shop/HMAC parameter confusion in any admin-facing HMAC check built on this shared utility, which is used to validate authenticity of admin/install callback-style requests.

### Likelihood Explanation
Likelihood is medium: the internal `oauth.callback()` flow in `lib/auth/oauth/oauth.ts` is not directly exposed to this issue because it always reconstructs the query via `URL(...).searchParams` and `Object.fromEntries(query.entries())` before calling `validQuery`/`validateHmac`, which collapses duplicate keys to their last value rather than an array. However, `shopify.utils.validateHmac` is a public, documented utility, and the asymmetry between the `admin` and `appProxy` normalization branches is a genuine, unpatched inconsistency reachable by any external caller feeding it a plain object with duplicate keys (e.g., raw framework request query objects), matching the "outbound/inbound request-authentication handler" category.

### Recommendation
Apply the same array/duplicate-parameter guard used for `appProxy` (`hmac`, `shop`, `signature`, `timestamp`) unconditionally to the `admin` signator branch in `normalizeQuery()`, so both code paths enforce the identical invariant instead of only one of them.

### Proof of Concept
```ts
import {shopifyApi} from '@shopify/shopify-api';

const shopify = shopifyApi(/* config with a known apiSecretKey */);

// Simulates req.query from an Express app using the default `qs` parser
// for a URL like: /auth/callback?shop=my-shop.myshopify.com&shop=attacker-shop.myshopify.com&hmac=<validHmacForFirstShop>&...
const query = {
  shop: ['my-shop.myshopify.com', 'attacker-shop.myshopify.com'],
  code: 'abc',
  state: 'nonce',
  timestamp: String(Math.trunc(Date.now() / 1000)),
  hmac: 'validHmacComputedOverFirstShopValue',
};

// signator defaults to 'admin'; normalizeQuery() performs no Array.isArray check here,
// unlike the appProxy branch, which throws "must not appear more than once."
const result = await shopify.utils.validateHmac(query);
// No InvalidHmacError is thrown for the duplicated `shop`, unlike the equivalent appProxy test:
// packages/apps/shopify-api/lib/utils/__tests__/hmac-validator.test.ts:227-244
``` [4](#0-3)

### Citations

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L84-115)
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

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L203-232)
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

  if (
    Math.abs(getCurrentTimeInSec() - parsedTimestamp) >
    HMAC_TIMESTAMP_PERMITTED_CLOCK_TOLERANCE_SEC
  ) {
    throw new ShopifyErrors.InvalidHmacError(
      'HMAC timestamp is outside of the tolerance range',
    );
  }
}
```

**File:** packages/apps/shopify-api/lib/utils/__tests__/hmac-validator.test.ts (L227-244)
```typescript
    test.each(['hmac', 'shop', 'signature', 'timestamp'])(
      'rejects a repeated security param: %s',
      async (param) => {
        const shopify = shopifyApi(testConfig());
        const query = new URLSearchParams({
          ...queryParams,
          hmac: 'unused',
          signature: 'unused',
        });
        query.append(param, 'duplicate');

        await expect(
          shopify.utils.validateHmac(query, options),
        ).rejects.toThrow(
          `Query parameter "${param}" must not appear more than once.`,
        );
      },
    );
```
