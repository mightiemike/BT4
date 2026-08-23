### Title
App Proxy HMAC signature verification uses non-injective query canonicalization, allowing request/parameter smuggling past signature validation - (File: packages/apps/shopify-api/lib/utils/hmac-validator.ts)

### Summary
The App Proxy signature check (`signator: 'appProxy'`) canonicalizes the query string for HMAC computation by joining array-valued parameters with a bare comma (`value.join(',')`) with no escaping of commas that may already be present inside individual values. This makes the canonical string non-injective: two semantically different query payloads (a single string value containing commas vs. multiple array-valued entries) serialize to the identical string and therefore produce the identical HMAC/signature. This is the same root-cause pattern as the reference report — an externally influenced value is trusted/consumed by a security-critical function without validating that its structure/identity is unambiguous, letting an attacker substitute different real data while passing the check.

### Finding Description
`stringifyQueryForAppProxy` builds the string that is HMAC'd (and compared against Shopify's signed `signature` param) like this: [1](#0-0) 

Only `hmac`, `shop`, `signature`, and `timestamp` are prevented from being arrays (`APP_PROXY_SINGLE_VALUE_PARAMS`); every other query parameter can be either a single string or an array, and both cases collapse to the same joined representation once a comma is present: [2](#0-1) [3](#0-2) 

`validateHmac` then recomputes the HMAC over this ambiguous canonical string and does a constant-time compare against the `signature` supplied on the (public, unauthenticated) App Proxy request: [4](#0-3) 

Because the canonicalization is not injective, a request whose parameter *structure* differs from what Shopify actually signed (e.g., `ids[]=1,2` as one array element containing a comma vs. `ids[]=1&ids[]=2` as two elements, or a scalar `note=a,b` vs. an array `note[]=a&note[]=b`) can still pass `validateHmac`, since both forms hash to the same string. An attacker who can observe one legitimately Shopify-signed App Proxy URL (these are plain GET URLs visible to any storefront visitor/browser) can restructure the query parameters around embedded commas and resubmit it; the signature check in `hmac-validator.ts` will still report it as valid, even though the parsed parameter shape received by the app differs from what Shopify actually authorized.

### Impact Explanation
The App Proxy endpoint is reachable by any anonymous storefront visitor — it requires no session, no login, and no privileged role, matching the "single merchant/customer" unprivileged threat model. Since the library's own signature-verification primitive (`validateHmac`/`generateLocalHmac` with `signator: 'appProxy'`) is the trust boundary apps rely on to accept App Proxy requests as authentically originating from Shopify, a canonicalization collision here constitutes acceptance of a request whose actual parameter content diverges from what was signed — i.e., an accepted forged/altered Shopify request, directly analogous to the original report's "owner can pass unvalidated data that is implicitly trusted" root cause, except achievable by an unauthenticated actor here.

### Likelihood Explanation
Exploitation only requires: (1) any comma-bearing parameter value in an app's proxy query (common for list/ID params, search terms, or free text), and (2) network-level ability to modify the outgoing GET request's query string before it reaches the app (trivial for any client, since App Proxy requests are ordinary signed GET requests forwarded through Shopify's edge to the app's server, and the URL/query is visible and re-issuable by anyone who receives it, e.g. via browser devtools, logs, or a shared/forwarded link). No secret material is needed to perform the restructuring — only the ability to reformat an already-signed query.

### Recommendation
Make the App Proxy canonicalization injective and reject any ambiguity instead of silently accepting it:
- Escape/encode array elements before joining (e.g. percent-encode commas within individual values) so that a scalar containing a comma can never collide with a joined array.
- Alternatively, canonicalize using the actual key form (`key[]=v1&key[]=v2` sorted) rather than lossy `join(',')`, mirroring the injective approach already used in `stringifyQueryForAdmin`/`ProcessedQuery`.
- Reject requests where a parameter's raw/array shape cannot be unambiguously reconstructed from the canonical string.

### Proof of Concept
Given app secret `S` and an app proxy path handling `note`:
1. Shopify signs and forwards `GET /apps/proxy?note=a,b&shop=x.myshopify.com&timestamp=T&signature=SIG`, computed by `stringifyQueryForAppProxy` as canonical string `note=a,bshop=x.myshopify.comtimestamp=T` → `SIG`.
2. Attacker (any storefront visitor) resubmits `GET /apps/proxy?note[]=a&note[]=b&shop=x.myshopify.com&timestamp=T&signature=SIG` (still within the timestamp tolerance window).
3. In `hmac-validator.ts`, `normalizeQuery`/`stringifyQueryForAppProxy` joins the array `['a','b']` via `.join(',')`, producing the identical canonical string `note=a,bshop=x.myshopify.comtimestamp=T`, so `validateHmac` returns `true` even though the parsed shape of `note` (scalar vs. array) differs from what Shopify actually signed. [1](#0-0)

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

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L84-116)
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
