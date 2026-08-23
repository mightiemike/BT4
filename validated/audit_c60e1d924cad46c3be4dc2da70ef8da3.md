### Title
Ambiguous, delimiter-free key/value concatenation in app-proxy HMAC canonicalization enables hash/signature collisions - (File: `packages/apps/shopify-api/lib/utils/hmac-validator.ts`)

### Summary
The app-proxy HMAC canonicalization function `stringifyQueryForAppProxy` builds the string to be HMAC'd by concatenating `key` + `=` + `value` for every parameter with **no delimiter between distinct key/value pairs**, mirroring the exact root cause described in the reference finding (`abi.encodePacked` used with dynamic-length inputs before hashing, causing collisions). This is the same "variable-length concatenation without unambiguous boundaries" defect, just applied to HMAC canonicalization instead of Solidity ABI packing. [1](#0-0) 

### Finding Description
`stringifyQueryForAppProxy` sorts query keys alphabetically and reduces them into one string with `${acc}${key}=${value}`, i.e. `key1=value1key2=value2...`, with no `&` or other separator between pairs (unlike `stringifyQueryForAdmin`, which uses `URLSearchParams`/`ProcessedQuery` and does insert `&` separators): [2](#0-1) 

Because app-proxy requests can carry arbitrary application-defined query parameters (only `hmac`, `shop`, `signature`, `timestamp` are constrained to single values via `APP_PROXY_SINGLE_VALUE_PARAMS`), and both the parameter names and values are attacker/merchant-storefront controlled, two structurally different parameter sets (different key/value boundaries) can produce an **identical concatenated canonical string** and therefore an identical valid HMAC for a completely different set of semantic fields (e.g. `logged_in_customer_id`, `path_prefix`, or other custom app-defined parameters). This is functionally the same collision class as `abi.encodePacked(a, b)` colliding with a different `(a', b')` pair when boundaries shift, since there is no length-prefixing or delimiter to disambiguate where one field ends and the next begins.

`normalizeQuery` only guards against **repeated** security parameters (`hmac`, `shop`, `signature`, `timestamp`), not against boundary-shifting collisions across arbitrary parameter names: [3](#0-2) 

The resulting HMAC is checked with a safe, constant-time compare, so the collision is the only path to bypassing signature validation - `safeCompare` itself is not at fault: [4](#0-3) 

This canonicalization is reached directly from an anonymous app-proxy HTTP request: `authenticateAppProxyFactory` reads `shop` from the URL and calls `api.utils.validateHmac(searchParams, {signator: 'appProxy'})` without any additional canonicalization hardening, and grants an authenticated app-proxy context (including access to the merchant's offline session/admin & storefront clients) if the signature validates. [5](#0-4) [6](#0-5) 

### Impact Explanation
If exploitable, this would allow an attacker who can obtain (or legitimately generate, e.g., as their own anonymous/authenticated storefront customer) one valid `(query, signature)` pair to re-partition the parameter boundaries in a new request such that a different set of key/value pairs (e.g. a forged `logged_in_customer_id`, or different `path_prefix`/custom fields consumed by the app's own proxy route logic) still concatenates to the same canonical string and thus reuses the same valid signature. That is a request-forgery/authentication-bypass primitive against app-proxy consumers that trust `validateHmac(..., {signator: 'appProxy'})` to also vouch for the *content* of custom query parameters, not merely the `shop`/`timestamp`.

However, I could not confirm a concrete, fully-worked forged example within the scope of this analysis: crafting a colliding pair requires satisfying (a) identical concatenated bytes and (b) the alphabetical key-sort order still holding for the new key set, which is a real but non-trivial constraint that would need to be solved per-target-parameter-name. I did not find any place in shopify-app-js itself where the individual raw `logged_in_customer_id`/custom fields (as opposed to `shop`/`timestamp`) are trusted post-HMAC-validation for privileged decisions - `authenticate.ts` only extracts `shop` (read directly from `url.searchParams`, independent of canonicalization) before/after validation, and hands the rest of the query untouched to app code. So the confirmed, in-repo impact is limited to "the app-proxy signature can validate for a query whose custom fields were not the ones actually chosen/signed by Shopify," which is a genuine violation of the HMAC's integrity guarantee, but downstream impact depends on what the consuming app does with those custom fields (out of scope of this repo).

### Likelihood Explanation
Moderate-to-low. Exploitation requires the attacker to construct a colliding parameter set that (1) matches byte-for-byte the canonical string of an already-known-valid signature and (2) respects the alphabetical sort constraint for the new key set. This is a solvable but non-trivial string-construction problem, not a simple duplicate-parameter attack (which is already blocked by `APP_PROXY_SINGLE_VALUE_PARAMS` checks). The admin (`stringifyQueryForAdmin`) path is not affected because it correctly delimits key/value pairs with `&` via `URLSearchParams`.

### Recommendation
Use an unambiguous, delimited (or length-prefixed) canonicalization for the app-proxy HMAC, consistent with the admin path. Concretely, change `stringifyQueryForAppProxy` in `packages/apps/shopify-api/lib/utils/hmac-validator.ts` to join pairs with a delimiter that cannot appear unescaped in keys/values (e.g. `&` with proper encoding, matching Shopify's documented app-proxy signature scheme, or explicit escaping of `=`/separator characters within each key and value before concatenation), rather than direct string concatenation with no boundary marker.

### Proof of Concept
Conceptual (not independently executed against a live instance, since this requires solving a per-key-name string-splitting constraint):
1. Attacker observes/produces one legitimate app-proxy request+signature pair, e.g. keys sorted as `k1=v1k2=v2` → HMAC `S` computed by `generateLocalHmac`/Shopify over the exact string in `stringifyQueryForAppProxy`: [1](#0-0) 
2. Attacker crafts an alternate query object whose alphabetically-sorted key/value pairs, when concatenated with the same `${key}=${value}` scheme, produce the identical byte string as step 1 (shifting characters from one value across the `key=value` boundary into an adjacent key/value), yielding a different effective `logged_in_customer_id`/custom-field value while the same `S` still verifies via `safeCompare(hmac, localHmac)`: [7](#0-6) 
3. The forged request is submitted to `authenticateAppProxyFactory`, which validates it successfully and passes the (misrepresented) query parameters through to app-proxy handler logic: [8](#0-7) 

Note: I was not able to fully verify a working, concrete numeric collision example (i.e., a real forged `logged_in_customer_id` bypassing customer authentication) within this analysis; this would require step-by-step string construction and testing against the actual `generateLocalHmac`/`validateHmac` implementation to confirm real-world exploitability versus a theoretical canonicalization weakness.

### Citations

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L51-66)
```typescript
function stringifyQueryForAdmin(query: AuthQuery): string {
  const processedQuery = new ProcessedQuery();
  Object.keys(query)
    .sort((val1, val2) => val1.localeCompare(val2))
    .forEach((key: string) => processedQuery.put(key, query[key]));

  return processedQuery.stringify(true);
}

function stringifyQueryForAppProxy(query: AuthQuery): string {
  return Object.entries(query)
    .sort(([val1], [val2]) => val1.localeCompare(val2))
    .reduce((acc, [key, value]) => {
      return `${acc}${key}=${Array.isArray(value) ? value.join(',') : value}`;
    }, '');
}
```

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/public/appProxy/authenticate.ts (L17-58)
```typescript
  return async function authenticate(
    request: Request,
  ): Promise<AppProxyContext | AppProxyContextWithSession> {
    const url = new URL(request.url);
    const shop = url.searchParams.get('shop')!;
    logger.info('Authenticating app proxy request', {shop});

    if (!(await validateAppProxyHmac(params, url))) {
      logger.info('App proxy request has invalid signature', {shop});
      throw new Response(undefined, {
        status: 400,
        statusText: 'Bad Request',
      });
    }

    const session = await ensureValidOfflineSession(params, shop);

    if (!session) {
      logger.debug('Could not find offline session, returning empty context', {
        shop,
        ...Object.fromEntries(url.searchParams.entries()),
      });

      const context: AppProxyContext = {
        liquid,
        session: undefined,
        admin: undefined,
        storefront: undefined,
      };

      return context;
    }

    const context: AppProxyContextWithSession = {
      liquid,
      session,
      admin: adminClientFactory({params, session}),
      storefront: storefrontClientFactory({params, session}),
    };

    return context;
  };
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
