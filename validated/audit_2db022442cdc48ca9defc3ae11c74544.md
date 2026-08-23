### Title
HMAC canonicalization collision in `stringifyQueryForAppProxy` allows a genuine App Proxy signature to validate a different, attacker-modified parameter set - (File: `packages/apps/shopify-api/lib/utils/hmac-validator.ts`)

### Summary
`stringifyQueryForAppProxy` builds the string that is HMAC-signed/verified for Shopify App Proxy requests by sorting keys and concatenating `key=value` pairs with **no delimiter between pairs and no escaping of `=`/`,` inside values**. Because of this, two structurally different query-parameter sets can serialize to the exact same byte string, so a signature that Shopify genuinely issued for one parameter set also validates for a different, attacker-crafted parameter set that an app-proxy consumer (e.g. `authenticateAppProxyFactory`) will parse differently.

### Finding Description
`validateHmac` for `signator: 'appProxy'` computes the local HMAC over the string produced by: [1](#0-0) 
which sorts entries alphabetically by key and concatenates them as `${key}=${value}` with **no separator between successive pairs**, and joins array values with `,` without escaping.

`generateLocalHmac` feeds this string into the HMAC: [2](#0-1) 
(only `hmac`/`signature` are stripped; `shop`, `timestamp`, `path_prefix`, and any custom query params remain part of the signed material.)

Because `URLSearchParams`/query-string parsing only splits a pair on the **first** `=`, a raw query string such as:

```
?a=bc=d&shop=test.myshopify.com&timestamp=123&signature=<hmac>
```

parses to `{a: "bc=d", shop: ..., timestamp: ..., signature: ...}`, and canonicalizes (sorted) to the exact same string as:

```
?a=b&c=d&shop=test.myshopify.com&timestamp=123&signature=<hmac>
```

which parses to `{a: "b", c: "d", shop: ..., timestamp: ..., signature: ...}` — both produce the canonical string `a=bc=dshop=...timestamp=...`. Since `createSHA256HMAC` only sees this byte string, the **same signature is valid for both structurally distinct parameter sets**. `validateHmac` (`packages/apps/shopify-api/lib/utils/hmac-validator.ts:84-116`) and its consumer `validateAppProxyHmac` in `packages/apps/shopify-app-remix/src/server/authenticate/public/appProxy/authenticate.ts:86-134` have no additional check that ties the signature to the *exact* key/value structure — only to the ambiguous canonical string. `normalizeQuery` (`packages/apps/shopify-api/lib/utils/hmac-validator.ts:118-151`) does not reject `=`/`,` characters inside values, and does not detect that a key was merged/split.

Attack flow:
1. Attacker (an ordinary customer/visitor) obtains a genuine, Shopify-signed app-proxy URL — these are exposed in the storefront (theme JS, links, network requests) since App Proxy forwards Shopify-signed requests to the browser context.
2. Attacker rewrites the query string so that one legitimate `key=value&key2=value2` pair is merged into a single `key=value&key2=value2`-colliding string (e.g., folding `c=d` into `a`'s value as `a=bc=d`), while keeping `shop`, `timestamp`, and `signature` unchanged.
3. The app-proxy handler re-parses the (now different) query string via `URLSearchParams`/`validateHmac`, which recomputes the same canonical string and thus the same HMAC — validation **succeeds**.
4. Application code reading `req.query.c` (or `.a`) now observes different/missing values than what Shopify actually sent and signed, even though signature verification passed.

### Impact Explanation
This breaks the authenticity guarantee of App Proxy signature verification (Shopify bounty class: "Signature/HMAC forgery" via canonicalization collision). It allows an unprivileged party who has seen one genuine signed app-proxy request to construct a different, still-"validly-signed" request whose individual parameter values differ from what Shopify actually sent. Depending on what the specific app does with those parameters (e.g., customer identifiers, product/variant IDs, discount codes, or other business logic keyed off individual query params), this can lead to parameter-value substitution/confusion attacks passing as authentic Shopify-signed input.

### Likelihood Explanation
- No secret or privileged access is required; the attacker only needs one genuine signed App Proxy request, which is routinely visible to any storefront visitor/customer (network requests, links, form actions rendered on the page).
- The collision construction is trivial (no cryptographic collision needed — just omission of a delimiter and folding an `=`-containing value into an adjacent key), fully reproducible, and deterministic.
- It requires that an app actually reads more than one custom query parameter separately (the most common real-world App Proxy configuration), making it broadly applicable, not merely a corner case.

### Recommendation
Change `stringifyQueryForAppProxy` (and ideally `stringifyQueryForAdmin`) to use an unambiguous, injective canonical encoding, e.g., percent-encode keys/values and join pairs with `&` (matching the admin OAuth signing scheme, or standard query-string canonicalization), so no two distinct parameter sets can ever serialize to the same signed string. At minimum, reject/percent-encode raw `=`, `&`, and `,` characters that appear inside parameter values before concatenation, and delimit pairs explicitly.

### Proof of Concept
```typescript
import {generateLocalHmac, validateHmac} from '../../lib/utils/hmac-validator';
import {testConfig} from '../../__tests__/test-config';

test('stringifyQueryForAppProxy collision allows signature reuse across different params', async () => {
  const config = testConfig();
  const now = Math.trunc(Date.now() / 1000).toString();

  // Legitimate params Shopify actually signs: a='b', c='d'
  const legitimateParams: Record<string, string> = {
    shop: 'test.myshopify.com',
    timestamp: now,
    a: 'b',
    c: 'd',
  };

  const signature = await generateLocalHmac(config)(legitimateParams, 'appProxy');
  legitimateParams.signature = signature;

  // Sanity check: legitimate params validate
  expect(
    await validateHmac(config)(legitimateParams, {signator: 'appProxy'}),
  ).toBe(true);

  // Attacker-crafted params: single key 'a' whose value swallows 'c=d'
  // Raw query string: ?a=bc=d&shop=test.myshopify.com&timestamp=<now>&signature=<signature>
  const forgedParams: Record<string, string> = {
    shop: 'test.myshopify.com',
    timestamp: now,
    a: 'bc=d', // note: no separate 'c' key at all
    signature,
  };

  // The genuine signature for {a:'b', c:'d'} also validates {a:'bc=d'} with no 'c' key
  expect(
    await validateHmac(config)(forgedParams, {signator: 'appProxy'}),
  ).toBe(true);
});
```

Expected result: both `validateHmac` calls return `true` for the same `signature`, even though the second parameter set has a different structure (`a` holds a different value and `c` is absent), proving the canonicalization collision and signature-reuse vulnerability.

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
