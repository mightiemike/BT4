### Title
Non-injective canonicalization in `stringifyQueryForAppProxy` allows HMAC/signature reuse across semantically different app-proxy requests - (File: `packages/apps/shopify-api/lib/utils/hmac-validator.ts`)

### Summary
`stringifyQueryForAppProxy` concatenates sorted `key=value` pairs with **no separator** between pairs and does not escape `=` or `,` inside keys/values. Two distinct query multimaps can be crafted whose concatenated canonical strings are byte-identical, so a single legitimately-signed app-proxy request (captured by any low-privileged user, e.g. their own browser visit) can be replayed as a differently-keyed forged request that still passes `validateHmac`.

### Finding Description
`stringifyQueryForAppProxy` builds the string to be HMAC'd as: [1](#0-0) 
i.e. `sortedKey1=value1` immediately followed by `sortedKey2=value2` with no `&` or other separator, and array values joined with `,`. `normalizeQuery` only rejects duplicate occurrences of the four reserved keys (`hmac`, `shop`, `signature`, `timestamp`); every other key/value is attacker-controllable and unescaped: [2](#0-1) 

Because there is no separator character reserved between adjacent `key=value` segments, the boundary between one pair's value and the next pair's key is not injective. For example, the two distinct multimaps
- `{a: "1", b: "2"}`
- `{a: "1b=2"}` (no `b` key at all)

both canonicalize (after alphabetical sort) to the identical string `a=1b=2`, so `createSHA256HMAC(secret, ...)` produces the same signature for both. This is confirmed by the existing repo test itself, which shows the exact no-separator, comma-joined format: [3](#0-2) 

`validateHmac` recomputes the local HMAC from whatever query the caller passes in and only compares the digest via `safeCompare`; it never verifies that the canonical string is an injective/unambiguous representation of the actual key/value pairs: [4](#0-3) 

Attack flow: an attacker (e.g. a logged-in storefront customer) makes one ordinary, legitimate app-proxy request and captures the resulting fully-signed URL from their own browser (`.../apps/my_app?logged_in_customer_id=...&path_prefix=...&shop=...&timestamp=...&signature=...`) — no secret or privileged access is needed to obtain this. They then craft a second, differently-keyed HTTP request straight to the app's proxy endpoint (bypassing a second trip through Shopify) where the standard, unambiguous `&`-delimited query string encodes a *different* semantic multimap (e.g. merges/removes a key by shifting the delimiter into a value) that happens to canonicalize to the exact same string as the original. `validateHmac` will accept the forged request with the reused `signature`/`timestamp`, as long as the timestamp is still within the 90-second tolerance window.

### Impact Explanation
This breaks the AUTHENTICITY guarantee of app-proxy signature verification: a signature computed by Shopify for one query can be reused to authenticate a different, attacker-chosen query against the app. Depending on how the app consumes `path_prefix`, `logged_in_customer_id`, or other custom proxy parameters, this can let an attacker inject or suppress fields (e.g. cause `path_prefix` to disappear, or shift characters between customer-supplied fields) while the request still passes as "HMAC valid," enabling logic confusion in app-proxy handlers that trust validated parameters. This corresponds to Shopify's "forged/replayed webhook or app-proxy signature" bounty class.

### Likelihood Explanation
- No secret, no MITM, no privileged role is required; a single unprivileged customer/attacker can trigger one legitimate app-proxy request through the storefront themselves and read the resulting signed URL from their own browser.
- The 90-second timestamp tolerance is enforced (`validateHmacTimestamp`), so the forged request must be sent promptly after capture, which is trivially automatable.
- Only non-reserved keys (anything other than `hmac`/`shop`/`signature`/`timestamp`) can be manipulated, but apps commonly pass through additional custom query parameters (`path_prefix`, `logged_in_customer_id`, app-specific params), giving attackers a workable surface, and the required alphabetical-sort-preserving key names are easy to choose.

### Recommendation
Make `stringifyQueryForAppProxy` (and ideally `stringifyQueryForAdmin`) injective: join key/value pairs with an unambiguous separator not permitted to appear un-escaped in keys/values (or percent-encode keys/values and use `&`/`=` consistently, matching how the raw query string was originally delimited), and reject keys/values containing the separator/`=` if literal Shopify parity requires the current unescaped format review. At minimum, add length-prefixed or escaped encoding so that no two distinct multimaps can produce the same canonical string.

### Proof of Concept
```javascript
// packages/apps/shopify-api/lib/utils/__tests__/hmac-validator.test.ts (new test)
test('signature collision across differently-keyed app-proxy queries', async () => {
  const shopify = shopifyApi(testConfig({apiSecretKey: 'my super secret key'}));
  const timestamp = String(getCurrentTimeInSec() - 1);

  // "legitimate" query: two separate keys a=1, b=2
  const legitCanonical = `a=1b=2shop=the shop URLtimestamp=${timestamp}`;
  const signature = createHmacSignature(legitCanonical, shopify.config.apiSecretKey);

  // "forged" query: single key a="1b=2" (no `b` key at all) -> same canonical string
  const forgedQuery = {
    a: '1b=2',
    shop: 'the shop URL',
    timestamp,
    signature,
  };

  await expect(
    shopify.utils.validateHmac(forgedQuery, {signator: 'appProxy'}),
  ).resolves.toBe(true); // accepted, despite `b` key being fabricated/removed
});
```
Expected result: `validateHmac` returns `true` for the forged, differently-keyed query using a signature that was never computed over it, demonstrating the canonicalization collision.

### Citations

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

**File:** packages/apps/shopify-api/lib/utils/__tests__/hmac-validator.test.ts (L207-225)
```typescript
    test('accepts URLSearchParams and preserves repeated application params', async () => {
      const shopify = shopifyApi(
        testConfig({apiSecretKey: 'my super secret key'}),
      );
      const query = new URLSearchParams(queryParams);
      query.append('consentGiven', 'true');
      query.append('consentGiven', 'false');
      query.set(
        'signature',
        createHmacSignature(
          `consentGiven=true,falselogged_in_customer_id=1path_prefix=/apps/my_appshop=the shop URLtimestamp=${queryParams.timestamp}`,
          shopify.config.apiSecretKey,
        ),
      );

      await expect(shopify.utils.validateHmac(query, options)).resolves.toBe(
        true,
      );
    });
```
