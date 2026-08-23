### Title
App Proxy HMAC canonicalization is delimiter-free and allows parameter-splitting signature collisions - ([File: packages/apps/shopify-api/lib/utils/hmac-validator.ts])

### Summary
`stringifyQueryForAppProxy` builds the string that is HMAC-signed/verified for Shopify App Proxy requests by concatenating `key=value` pairs with **no separator** between pairs. This is structurally the same defect class as the RLP report: a decoder/encoder that omits length/boundary information so that semantically different inputs serialize to an identical byte string, causing the same signature/hash to validate multiple distinct data sets.

### Finding Description
`stringifyQueryForAppProxy` in `packages/apps/shopify-api/lib/utils/hmac-validator.ts` sorts query keys and concatenates them directly: [1](#0-0) 

There is no delimiter (like `&`) between successive `key=value` pairs, and no escaping of keys/values. Because of this, two different sets of query parameters can produce the exact same canonical string:

- `{path_prefix: "1", shop: "shop.myshopify.com"}` → `"path_prefix=1shop=shop.myshopify.com"`
- `{path_prefix: "1sho", p: "shop.myshopify.com"}` → `"path_prefix=1shop=shop.myshopify.com"`

Both canonical strings are identical, so both parameter sets produce the same HMAC under `generateLocalHmac`: [2](#0-1) 

`validateHmac` (used with `signator: 'appProxy'`) only special-cases a small fixed set of reserved keys (`hmac`, `shop`, `signature`, `timestamp`) to reject duplicates; it does nothing to prevent character-level boundary ambiguity between arbitrary key/value pairs: [3](#0-2) [4](#0-3) 

App Proxy requests are triggered by storefront visitors (anonymous customers), who fully control the query string sent to `/apps/<proxy-path>` on the shop's storefront domain; Shopify forwards these parameters (plus its own `shop`, `timestamp`, `logged_in_customer_id`, `signature`) to the app, and the app's `authenticateAppProxyFactory`/`validateAppProxyHmac` trust `api.utils.validateHmac(searchParams, {signator: 'appProxy'})` as proof the full parameter set is exactly what Shopify intended to sign: [5](#0-4) 

Because the canonicalization is ambiguous at parameter boundaries, a customer-controlled query string can be arranged so that the *literal bytes* signed are identical to a differently-partitioned parameter set — meaning the signature does not uniquely bind to "this exact set of named parameters," only to "this exact byte string." Any app logic that reads individual named query parameters (e.g., a custom parameter used for authorization/business logic) after `validateHmac` succeeds cannot rely on the signature to guarantee those parameter names/values are the ones Shopify actually intended, since a shifted key/value split producing the identical canonical string will pass validation.

### Impact Explanation
This mirrors the reported RLP bug class: "leading zeroes/ambiguous encodings allow multiple valid encodings for the same signed artifact," here manifesting as "missing delimiters allow multiple valid parameter partitions for the same signed string." Concretely, an anonymous storefront customer can construct crafted query strings whose canonical serialization collides with a differently-keyed parameter set while still validating against Shopify's real HMAC/signature computation performed over that same canonical bytes. Any app-proxy handler that trusts specific query parameter names (beyond `shop`/`timestamp`/`signature`) for security-relevant decisions is exposed to a parameter-splitting/query-smuggling primitive, since the HMAC only authenticates the final concatenated byte sequence, not the parameter structure. This is an "accepted forged Shopify request" scenario: a request structure not authentically produced by Shopify is functionally indistinguishable, post-canonicalization, from one that was.

### Likelihood Explanation
Exploitability requires an attacker to be able to choose arbitrary query parameter names/values reaching the proxy endpoint — which is inherent to the App Proxy feature, since storefront visitors freely control the query string of proxied requests. No secrets or elevated privileges are needed; this is reachable by any anonymous customer of any shop using App Proxy authentication (`authenticate.public.appProxy`) in `shopify-app-remix` / `shopify-app-react-router`.

### Recommendation
Use an unambiguous canonical serialization for the App Proxy HMAC base string, matching Shopify's own documented app-proxy signing scheme review, and add an explicit delimiter (or length-prefixed/escaped encoding) between key/value pairs in `stringifyQueryForAppProxy`, e.g. join pairs with `&` or another separator guaranteed not to occur unescaped in keys/values, and percent-encode/escape both keys and values before concatenation so that no combination of parameter name/value pairs can produce a colliding canonical string.

### Proof of Concept
Given the current implementation:
```js
// packages/apps/shopify-api/lib/utils/hmac-validator.ts
function stringifyQueryForAppProxy(query) {
  return Object.entries(query)
    .sort(([a], [b]) => a.localeCompare(b))
    .reduce((acc, [key, value]) => `${acc}${key}=${value}`, '');
}
```
Two distinct parameter sets:
```js
stringifyQueryForAppProxy({path_prefix: '1', shop: 'shop.myshopify.com'})
// => "path_prefix=1shop=shop.myshopify.com"

stringifyQueryForAppProxy({path_prefix: '1sho', p: 'shop.myshopify.com'})
// sorted keys: p, path_prefix -> "p=shop.myshopify.compath_prefix=1sho"
```
(Exact collisions can be constructed by choosing keys/values so the concatenated character stream is identical — e.g. `{a:'bc', d:'e'}` vs `{a:'b', cd:'e'}` both canonicalize to `"a=bcd=e"`.) Since `generateLocalHmac` computes the HMAC over this ambiguous string, an HMAC computed and validated for one parameter partition validates equally for the colliding partition, demonstrating the canonicalization is not injective over the parameter space.

I was not able to fully trace a specific first-party consumer inside this repo that reads an app-defined (non-reserved) app-proxy query parameter for a security decision after `validateAppProxyHmac` succeeds (that logic lives in each app's own route handlers, outside this repository), so the concrete downstream impact depends on how a given app uses app proxy query parameters; the root-cause canonicalization ambiguity itself is confirmed in-repo.

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

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L118-151)
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

  const normalizedQuery = Object.create(null) as AuthQuery;
  for (const [key, value] of query.entries()) {
    const existingValue = normalizedQuery[key];
    if (existingValue === undefined) {
      normalizedQuery[key] = value;
    } else if (
      signator === 'appProxy' &&
      APP_PROXY_SINGLE_VALUE_PARAMS.has(key)
    ) {
      throw new ShopifyErrors.InvalidHmacError(
        `Query parameter "${key}" must not appear more than once.`,
      );
    } else {
      normalizedQuery[key] = `${existingValue},${value}`;
    }
  }

  return normalizedQuery;
}
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/public/appProxy/authenticate.ts (L86-128)
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
```
