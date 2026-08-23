### Title
Unbounded Query-Parameter Processing Causes Algorithmic-Complexity DoS in HMAC/Signature Validation Handlers - (File: `packages/apps/shopify-api/lib/utils/hmac-validator.ts`)

### Summary
The `validateHmac` function, which is invoked pre-authentication on the OAuth callback path and on every App Proxy request, parses and canonicalizes the *entire* incoming query string before checking or comparing the HMAC/signature. Because the query size and the number of repeated parameter keys are fully attacker-controlled and unbounded, an anonymous request can force the auth handler to perform expensive, superlinear string-building work before any credential is validated — the same "large-input causes disproportionate resource consumption before the security check can even run" bug class described in the referenced Gravity Bridge finding (large validator set makes `makeCheckpoint` unaffordable before it can execute).

### Finding Description
`validateHmac` first calls `normalizeQuery` on the raw query [1](#0-0) , which, for `URLSearchParams` input, iterates every entry and for repeated keys concatenates values with `${existingValue},${value}` [2](#0-1) . This runs unconditionally, before the presence of `hmac`/`signature` is even checked (those checks happen afterward at lines 91–101) [3](#0-2) .

After normalization, `generateLocalHmac` builds the canonical string to sign via `stringifyQueryForAdmin`/`stringifyQueryForAppProxy`, both of which sort and reduce over *all* query keys, repeatedly concatenating onto a growing string/accumulator [4](#0-3) . Only after this full-cost canonicalization does the code perform the actual `safeCompare` of the HMAC [5](#0-4) .

Two concrete unauthenticated entry points reach this code with fully attacker-supplied, unbounded query data:
1. **App Proxy requests** — `authenticateAppProxyFactory` builds a `URLSearchParams` directly from the incoming request URL and passes it straight into `api.utils.validateHmac` (up to three times per request, on retry paths) with no size/parameter-count limits before any signature check succeeds [6](#0-5) .
2. **OAuth callback** — `callback()` parses the raw request URL's `searchParams` and passes the resulting query object to `validQuery`/`validateHmac` prior to any authentication succeeding [7](#0-6) [8](#0-7) .

There is no cap on query string length, number of parameters, or number of repeated keys anywhere in this path, so an attacker can submit an extremely large query (e.g., tens or hundreds of thousands of repeated parameter keys, or megabytes of query data) to either endpoint. Because JavaScript strings are immutable, repeated concatenation in `normalizeQuery`'s duplicate-key merging and in the `reduce`-based canonicalization scales poorly with input size, so processing cost grows non-linearly with attacker-controlled input size — all of it performed *before* the cheap, constant-time HMAC comparison that is supposed to reject invalid requests.

### Impact Explanation
An anonymous attacker can send oversized/duplicate-parameter requests to the App Proxy authentication handler or the OAuth callback handler, forcing the server to spend disproportionate CPU/memory on string processing before rejecting the (invalid) HMAC. Repeated concurrent requests of this shape can degrade or exhaust server resources dedicated to authentication processing, denying service to legitimate OAuth callbacks and app-proxy traffic — a DoS of an authentication handler, consistent with the "unbounded work before the security-critical check completes" class in the source report.

### Likelihood Explanation
Both entry points are reachable by a fully unauthenticated actor with a single crafted HTTP request (no valid shop, session, or credentials required), and neither imposes limits on query size or parameter repetition before doing the expensive canonicalization work. This makes the analog straightforward to trigger, limited only by whatever generic HTTP layer/proxy limits (e.g., max URL length) exist outside this library — which are not enforced within the library itself.

### Recommendation
- Enforce a maximum number of query parameters and a maximum total query length before calling `normalizeQuery`/`validateHmac`, rejecting oversized requests early with a cheap check.
- Avoid unbounded string concatenation for duplicate keys; instead, collect values in an array and join once, or reject requests containing an excessive number of duplicate keys outright.
- Perform a cheap presence/format check on `hmac`/`signature` and `timestamp` before any query normalization/canonicalization work, so malformed or missing-credential requests are rejected without incurring the full processing cost.

### Proof of Concept
1. Send a GET request to an app's App Proxy route (e.g. `/apps/my_app?shop=test.myshopify.com&timestamp=<now>&a=1&a=1&a=1...` repeated tens/hundreds of thousands of times for key `a`).
2. `authenticateAppProxyFactory` constructs `URLSearchParams` from the full query and calls `api.utils.validateHmac(searchParams, {signator: 'appProxy'})` [9](#0-8) .
3. `normalizeQuery` iterates all entries, repeatedly concatenating the duplicated `a` values into one ever-growing string [2](#0-1) , and `stringifyQueryForAppProxy` then reduces over the full parameter set again [10](#0-9) .
4. Because no `signature` is valid, the request is ultimately rejected with 400 — but only after the full, unbounded canonicalization cost has already been paid, which scales with attacker-chosen input size and can be repeated concurrently to exhaust server resources.

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

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L84-90)
```typescript
export function validateHmac(config: ConfigInterface) {
  return async (
    query: HmacQuery,
    {signator}: {signator: HMACSignator} = {signator: 'admin'},
  ): Promise<boolean> => {
    const normalizedQuery = normalizeQuery(query, signator);

```

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L91-101)
```typescript
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

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L133-148)
```typescript
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
```

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

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L143-147)
```typescript
    const query = new URL(
      request.url,
      `${config.hostScheme}://${config.hostName}`,
    ).searchParams;
    const shop = query.get('shop')!;
```

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L242-255)
```typescript
async function validQuery({
  config,
  query,
  stateFromCookie,
}: {
  config: ConfigInterface;
  query: AuthQuery;
  stateFromCookie: string;
}): Promise<boolean> {
  return (
    (await validateHmac(config)(query)) &&
    safeCompare(query.state!, stateFromCookie)
  );
}
```
