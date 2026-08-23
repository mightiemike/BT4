### Title
Resource-consumption DoS via unbounded duplicate-query-parameter processing in App Proxy HMAC validation - (File: `packages/apps/shopify-api/lib/utils/hmac-validator.ts`)

### Summary
The App Proxy authentication handler builds a plain object from every entry of an attacker-controllable `URLSearchParams` and performs multiple full loop/sort/concatenation passes over it **before** the HMAC signature is checked and rejected, mirroring the reported bug class ("allocations made in a loop before checking size/count of untrusted input").

### Finding Description
`authenticateAppProxyFactory` (`packages/apps/shopify-app-remix/src/server/authenticate/public/appProxy/authenticate.ts` / identical code in `shopify-app-react-router`) is the handler apps use to authenticate incoming App Proxy requests, which originate from anonymous storefront visitors forwarded through Shopify: [1](#0-0) 

It calls `api.utils.validateHmac(searchParams, {signator: 'appProxy'})` up to **three times per request** (initial call plus two retries with modified `_data` params) before finally rejecting an invalid request: [2](#0-1) 

Inside `validateHmac` (`packages/apps/shopify-api/lib/utils/hmac-validator.ts`), the very first step is `normalizeQuery()`, which iterates **every** entry of the raw `URLSearchParams` object into a plain JS object *before* the signature or timestamp is validated: [3](#0-2) 

Only four specific keys (`hmac`, `shop`, `signature`, `timestamp`) are protected from duplication: [4](#0-3) 

For any other key, duplicate occurrences are naively string-concatenated in the loop: `normalizedQuery[key] = `${existingValue},${value}`;`. Since each concatenation re-copies the entire growing string, N duplicates of an arbitrary key produce O(N) allocations whose cumulative size grows O(N²).

After normalization, `generateLocalHmac()` performs a second full pass — sorting all keys and reducing/concatenating them again via `stringifyQueryForAppProxy()` — over the same attacker-supplied object, again *before* the (deterministically failing, since the attacker lacks the app secret) signature comparison occurs: [5](#0-4) 

There is no check anywhere in this call path on the number of query parameters, the number of duplicate occurrences of a given key, or the total size of the query string before this expensive processing begins — the exact root-cause pattern described in the source report (unbounded loop-based allocation prior to a size/count check on untrusted input).

### Impact Explanation
An anonymous actor able to reach the app's App Proxy authentication endpoint (either directly on the app server, or via Shopify's proxy forwarding a crafted request) can send a single HTTP request whose query string contains a large number of duplicate, non-reserved parameter names/values. Each such request forces: (1) up to two O(N²) string-concatenation passes in `normalizeQuery`, (2) an additional sort + O(N²)-style reduce/concatenation in `stringifyQueryForAppProxy`, and (3) this entire sequence is repeated **up to three times** per request due to `validateAppProxyHmac`'s retry logic — all before the (guaranteed) rejection of an unsigned/invalid request. Because this occurs on the authentication handler for the App Proxy route, it can be triggered per-request by unauthenticated traffic and consumes CPU/memory ahead of any authentication success, degrading availability of the auth handler.

### Likelihood Explanation
Medium. Exploitability depends on how large a query string the app's hosting stack/HTTP framework permits (many Node/Express or edge-runtime deployments will accept multi-KB to multi-MB URLs), but no library-level limit exists to bound the number of duplicate parameters processed before rejection. The vulnerable code is on the standard, documented `authenticate.public.appProxy` code path used by any app that implements App Proxy endpoints, so it is broadly reachable without any prior authentication or privileged access.

### Recommendation
Before iterating/normalizing the query in `normalizeQuery()`/`validateHmac()`, enforce hard limits (e.g., a maximum total parameter count and/or maximum total query-string size) and reject requests exceeding them with a cheap, immediate rejection. Additionally, avoid the repeated O(N²) string-concatenation pattern for duplicate keys (e.g., accumulate values in an array and `join(',')` once) to remove the quadratic blowup, and consider limiting `validateAppProxyHmac`'s multiple retries to bound the amplification factor.

### Proof of Concept
1. Send a GET request to an app's App Proxy route, e.g. `/apps/proxy?shop=test.myshopify.com&timestamp=<now>&signature=invalid&x=1&x=1&x=1...` with the parameter `x` repeated tens of thousands of times (or many distinct large-value parameters) to maximize the query string within the host framework's URL-size limits.
2. `authenticateAppProxyFactory` invokes `validateAppProxyHmac`, which calls `api.utils.validateHmac(searchParams, {signator: 'appProxy'})` up to three times.
3. Each call executes `normalizeQuery()` (`packages/apps/shopify-api/lib/utils/hmac-validator.ts:118-151`), performing O(N²) string concatenation for the repeated `x` key, followed by `generateLocalHmac()`'s sort/reduce pass, before the invalid signature is finally rejected.
4. Repeated requests of this form measurably increase CPU time/memory usage on the app server per request, disproportionate to the cost of validating a normal, well-formed App Proxy request.

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/public/appProxy/authenticate.ts (L86-126)
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
```

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L26-31)
```typescript
const APP_PROXY_SINGLE_VALUE_PARAMS = new Set([
  'hmac',
  'shop',
  'signature',
  'timestamp',
]);
```

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L60-82)
```typescript
function stringifyQueryForAppProxy(query: AuthQuery): string {
  return Object.entries(query)
    .sort(([val1], [val2]) => val1.localeCompare(val2))
    .reduce((acc, [key, value]) => {
      return `${acc}${key}=${Array.isArray(value) ? value.join(',') : value}`;
    }, '');
}

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
