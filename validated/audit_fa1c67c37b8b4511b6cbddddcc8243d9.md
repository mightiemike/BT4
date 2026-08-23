### Title
HMAC forgery via delimiter-free key/value concatenation collision in `stringifyQueryForAppProxy` - ([File: packages/apps/shopify-api/lib/utils/hmac-validator.ts])

### Summary
`stringifyQueryForAppProxy` builds the string to be HMAC-signed by concatenating `key=value` pairs with no separator between pairs (`${acc}${key}=${value}`). Because there is no delimiter distinguishing where one pair ends and the next begins, two structurally and semantically different query parameter sets can serialize to the identical byte string, and therefore compute the identical HMAC. An attacker who can trigger Shopify to sign one parameter set can substitute a different parameter set that collides to the same string and have the app accept it as validly signed.

### Finding Description
`stringifyQueryForAppProxy` at [1](#0-0)  sorts entries by key and reduces them via plain string concatenation `key=value` with no separating character (no `&`, no length prefix, no escaping of `=` inside values). This is the same canonicalization Shopify's app-proxy signing uses, so the collision applies both to how Shopify computes the real signature server-side and how `generateLocalHmac`/`validateHmac` in this file recompute it via `signator: 'appProxy'` at [2](#0-1) .

Because the join has no delimiter, a value that happens to look like `key=value` fragments can be re-partitioned into a different key/value split that yields the exact same concatenated string. For example:
- Query A: `{a: 'z', bc: '1'}` → sorted keys `a, bc` → `"a=z" + "bc=1"` = `"a=zbc=1"`
- Query B: `{a: 'zb', c: '1'}` → sorted keys `a, c` → `"a=zb" + "c=1"` = `"a=zbc=1"`

Both produce the identical canonical string `"a=zbc=1"`, so `createSHA256HMAC(secret, "a=zbc=1", ...)` is identical for both, meaning a signature (`signature`) issued by Shopify for query A is also a valid signature for query B as far as `validateHmac`/`generateLocalHmac` are concerned — even though the two parameter sets have different key names and different values.

This is reachable by an unprivileged attacker: the Shopify App Proxy endpoint (`/apps/<subpath>/...`) is a public storefront-facing URL that forwards arbitrary query parameters chosen by any anonymous visitor/customer, and Shopify signs whatever parameter set it receives before forwarding to the app. `normalizeQuery`/`APP_PROXY_SINGLE_VALUE_PARAMS` in the same file at [3](#0-2)  only guards against duplicate `hmac`/`shop`/`signature`/`timestamp`, not against this key/value re-partitioning collision, so it does not stop the attack. `safeCompare` at the end of `validateHmac` only prevents timing side-channels; it does not detect that the two different query objects hashed to the same canonical string. `validateAppProxyHmac` in `authenticateAppProxyFactory` at [4](#0-3)  passes the raw `URLSearchParams` straight into `api.utils.validateHmac(..., {signator: 'appProxy'})` with no additional canonicalization check, so the collision propagates directly into the app's authentication decision.

### Impact Explanation
An attacker who obtains one legitimately Shopify-signed app-proxy request (trivial: they generate it themselves by visiting the proxy URL with attacker-chosen custom query parameters, since App Proxy requests are signed for whatever query string the visitor sends) can re-partition the key/value boundaries to smuggle a *different* set of parameter names/values under the same `signature`. Because the app's business logic reads `url.searchParams` (the actual, attacker-supplied key names) rather than the canonical string, this allows forging distinct application-visible parameter names/values (e.g., substituting a different custom parameter name for one the app trusts because it was "validly signed") while `validateAppProxyHmac` still returns `isValid = true`. This is a signature/authenticity-bypass class issue (forged/mismatched signed request accepted) impacting any custom app-proxy parameter that isn't in the fixed single-value set (`hmac`, `shop`, `signature`, `timestamp`).

### Likelihood Explanation
No secret or privileged access is required — only the ability to send arbitrary query parameters to a public app-proxy URL, which is available to any anonymous storefront visitor. Constructing a colliding key/value pair only requires that a value's suffix matches `nextkey=`, which is easy to engineer deliberately (as shown in the PoC) whenever the app or a custom integration passes attacker-influenced strings as query values that get forwarded through the proxy. The main precondition is that the app's proxy handler trusts/uses a custom query parameter whose name or value can be substituted via this collision technique; this significantly depends on how the specific app built on the library uses the (validated) query, which is outside this library's control — the library itself, however, does emit a `true` validation result for two distinct parameter sets, which is the exploitable defect.

### Recommendation
Fix `stringifyQueryForAppProxy` (and any other canonicalization) to use an unambiguous, escaping/delimiter-safe serialization — e.g., percent-encode both key and value (as already done for the admin variant via `ProcessedQuery`) and join pairs with an explicit separator such as `&`, so that `key=value&key2=value2` cannot be re-partitioned into a different key/value split. At minimum, reject or percent-encode values/keys containing `=` before concatenation.

### Proof of Concept
```ts
import {stringifyQueryForAppProxy_forTest as stringifyQueryForAppProxy} from '../hmac-validator'; // expose for test, or inline the function

// Query A and Query B are structurally different (different key sets/values)
const queryA = {a: 'z', bc: '1'};
const queryB = {a: 'zb', c: '1'};

test('colliding app-proxy query strings produce identical canonical string', () => {
  const strA = stringifyQueryForAppProxy(queryA);
  const strB = stringifyQueryForAppProxy(queryB);
  expect(strA).toBe('a=zbc=1');
  expect(strB).toBe('a=zbc=1');
  expect(strA).toEqual(strB); // collision confirmed despite different keys/values
});

// End-to-end: a signature computed for queryA validates queryB as well
test('validateHmac accepts queryB using a signature generated for queryA', async () => {
  const shopify = shopifyApi(testConfig({apiSecretKey: 'secret'}));
  const timestamp = String(getCurrentTimeInSec());
  const fullA = {...queryA, timestamp};
  const fullB = {...queryB, timestamp};

  const signature = await shopify.utils.generateLocalHmac(fullA, 'appProxy'); // simulates Shopify's signature for A
  const forgedB = {...fullB, signature};

  await expect(
    shopify.utils.validateHmac(forgedB, {signator: 'appProxy'}),
  ).resolves.toBe(true); // accepts a different parameter set under A's signature
});
```

### Citations

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L26-31)
```typescript
const APP_PROXY_SINGLE_VALUE_PARAMS = new Set([
  'hmac',
  'shop',
  'signature',
  'timestamp',
]);
```

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L60-66)
```typescript
function stringifyQueryForAppProxy(query: AuthQuery): string {
  return Object.entries(query)
    .sort(([val1], [val2]) => val1.localeCompare(val2))
    .reduce((acc, [key, value]) => {
      return `${acc}${key}=${Array.isArray(value) ? value.join(',') : value}`;
    }, '');
}
```

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L68-116)
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
}
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/public/appProxy/authenticate.ts (L86-134)
```typescript
async function validateAppProxyHmac(
  params: BasicParams,
  url: URL,
): Promise<boolean> {
  const {api, logger} = params;

  try {
    let searchParams = new URLSearchParams(url.search);
    if (!searchParams.get('index')) {
      searchParams.delete('index');
    }

    let isValid = await api.utils.validateHmac(searchParams, {
      signator: 'appProxy',
    });

    if (!isValid) {
      const cleanPath = url.pathname
        .replace(/^\//, '')
        .replace(/\/$/, '')
        .replaceAll('/', '.');
      const data = `routes%2F${cleanPath}`;

      searchParams = new URLSearchParams(
        `?_data=${data}&${searchParams.toString().replace(/^\?/, '')}`,
      );

      isValid = await api.utils.validateHmac(searchParams, {
        signator: 'appProxy',
      });

      if (!isValid) {
        const searchParams = new URLSearchParams(
          `?_data=${data}._index&${url.search.replace(/^\?/, '')}`,
        );

        isValid = await api.utils.validateHmac(searchParams, {
          signator: 'appProxy',
        });
      }
    }

    return isValid;
  } catch (error) {
    const shop = url.searchParams.get('shop')!;
    logger.info(error.message, {shop});
    throw new Response(undefined, {status: 400, statusText: 'Bad Request'});
  }
}
```
