### Title
OAuth callback resolves `shop`/`code` from raw `URLSearchParams.get()` (first-wins) while `validateHmac` signs a duplicate-collapsed `Object.fromEntries` query (last-wins), letting an attacker HMAC-authenticate one shop/code pair while the token exchange executes against a different one - (File: packages/apps/shopify-api/lib/auth/oauth/oauth.ts)

### Summary
`callback()` in `oauth.ts` builds `authQuery` via `Object.fromEntries(query.entries())`, which silently keeps the **last** occurrence of a duplicated key, and passes that object to `validateHmac`. Immediately after, it independently calls `query.get('shop')` / `query.get('code')` on the original `URLSearchParams`, which return the **first** occurrence of a duplicated key. `normalizeQuery` in `hmac-validator.ts` only rejects duplicate `hmac`/`shop`/`signature`/`timestamp` keys for the `appProxy` signator - the `admin` OAuth path has no such protection at all.

### Finding Description
In `packages/apps/shopify-api/lib/auth/oauth/oauth.ts`:
- `const shop = query.get('shop')!;` (line 147) and `sanitizeShop(config)(query.get('shop')!, true)!` (line 194) both use `URLSearchParams.get()`, which returns the **first** value for a repeated key.
- `const authQuery: AuthQuery = Object.fromEntries(query.entries());` (line 178) collapses a repeated key to its **last** value, because each iteration simply overwrites the object property. This `authQuery` (not a `URLSearchParams` instance any more) is what's passed into `validateHmac(config)(authQuery)`. [1](#0-0) [2](#0-1) 

Inside `hmac-validator.ts`, `normalizeQuery` only checks `APP_PROXY_SINGLE_VALUE_PARAMS` when `query instanceof URLSearchParams` **and** `signator === 'appProxy'`. Since `authQuery` reaching `validateHmac` from the OAuth callback is a plain object (already collapsed by `Object.fromEntries`) and the signator is `'admin'`, no duplicate-key check ever runs; `normalizeQuery` just returns the object unchanged, and `stringifyQueryForAdmin` signs whatever single "last-wins" value happens to be there. [3](#0-2) [4](#0-3) 

Exploit flow: an attacker completes a normal OAuth install for their own shop (`attacker.myshopify.com`) and captures the genuine, Shopify-signed callback query string (`shop`, `code`, `state`, `timestamp`, `hmac`). They then send this crafted query directly to the app's callback endpoint with the `shop` parameter duplicated, e.g.:
`?shop=victim.myshopify.com&shop=attacker.myshopify.com&code=<attacker's real code>&state=<attacker's state>&timestamp=...&hmac=<original valid hmac>`
- `query.get('shop')` returns `victim.myshopify.com` (first) → used to compute `cleanShop`, which is the host the token-exchange POST is sent to.
- `authQuery.shop` (from `Object.fromEntries`) resolves to `attacker.myshopify.com` (last) → this is what `validateHmac` actually verifies, and it matches the original, still-valid HMAC.

Result: `validateHmac` reports the request as authentic for `attacker.myshopify.com`, but the code proceeds to POST the access-token exchange to `https://victim.myshopify.com/admin/oauth/access_token` using `cleanShop = victim.myshopify.com` while `body.code` is the attacker's own code. The value that is cryptographically verified (shop bound in the signed query) is not the value the code subsequently acts on (shop used for the outbound request and resulting `Session`).

### Impact Explanation
This breaks the core invariant that HMAC verification must bind to the exact value the app subsequently trusts and acts upon. Concretely, the shop the library treats as "HMAC-authenticated" and the shop it actually issues the access-token request to / creates a `Session` for can diverge. Whether this culminates in a full cross-tenant token/session compromise ultimately depends on Shopify's OAuth backend rejecting a code/shop mismatch (external system, not verifiable from this repo), but the local authenticity-binding violation itself is a real defect: it removes the guarantee that "the shop verified by HMAC" equals "the shop the token exchange and resulting Session are created for," which is exactly the property `APP_PROXY_SINGLE_VALUE_PARAMS` was introduced to protect for the app-proxy path but was never extended to the `admin` OAuth callback path.

### Likelihood Explanation
- The callback endpoint is public/unauthenticated by design (that's the point of OAuth callback), so any unprivileged actor can send arbitrary query strings to it directly, bypassing the Shopify redirect. [5](#0-4) 
- The attacker only needs a normal, unprivileged capability: install the app on any shop they control to obtain one genuine, HMAC-signed callback query, then replay it with a duplicated `shop` parameter. No secret, no privileged role, no MITM is required.
- `normalizeQuery`'s only duplicate-key defense is scoped to `appProxy`, so nothing in the current `admin`/OAuth path stops this. [3](#0-2) 
- The library-level divergence (HMAC-verified value vs. downstream-used value) is deterministically reproducible with a Jest test; whether Shopify's real `/admin/oauth/access_token` endpoint would honor a code issued for a different shop domain is outside this repo and not verifiable here, which limits certainty about the ultimate end-to-end impact.

### Recommendation
Normalize the query exactly once, at the top of `callback()`, using the same duplicate-key policy everywhere (reject duplicates for `shop`, `code`, `state`, `hmac`, `timestamp` outright, the same way `APP_PROXY_SINGLE_VALUE_PARAMS` does for app proxy), and derive `shop`, `code`, and the object passed to `validateHmac` from that single normalized result instead of mixing `URLSearchParams.get()` calls with a separately-collapsed `Object.fromEntries(query.entries())` object.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/auth/oauth/__tests__/duplicate-shop.test.ts
import {URLSearchParams} from 'url';

test('shop used for HMAC verification diverges from shop used for token exchange', () => {
  const rawQuery = new URLSearchParams();
  rawQuery.append('shop', 'victim.myshopify.com');   // duplicate #1
  rawQuery.append('shop', 'attacker.myshopify.com');  // duplicate #2 (matches signed hmac)
  rawQuery.set('code', 'attacker-real-code');
  rawQuery.set('state', 'attacker-state');
  rawQuery.set('timestamp', String(Math.trunc(Date.now() / 1000)));
  rawQuery.set('hmac', '<valid-hmac-for-attacker.myshopify.com-query>');

  // Mirrors oauth.ts callback() exactly:
  const shopUsedForTokenExchange = rawQuery.get('shop'); // -> 'victim.myshopify.com'
  const authQuery = Object.fromEntries(rawQuery.entries());
  const shopUsedForHmacValidation = authQuery.shop;       // -> 'attacker.myshopify.com'

  // Invariant violated: HMAC binds to a different shop than the one acted upon.
  expect(shopUsedForHmacValidation).not.toEqual(shopUsedForTokenExchange);
});
```
This demonstrates, without needing `apiSecretKey`, that `oauth.ts`'s `cleanShop` (line 194, from `query.get('shop')`) and the `shop` field actually verified by `validateHmac` (line 179, from `authQuery.shop`) can be made to differ by duplicating the `shop` query parameter, violating the required authenticity binding.

### Citations

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L129-147)
```typescript
export function callback(config: ConfigInterface): OAuthCallback {
  return async function callback<T = AdapterHeaders>({
    expiring,
    ...adapterArgs
  }: CallbackParams): Promise<CallbackResponse<T>> {
    throwIfCustomStoreApp(
      config.isCustomStoreApp,
      'Cannot perform OAuth for private apps',
    );

    const log = logger(config);

    const request = await abstractConvertRequest(adapterArgs);

    const query = new URL(
      request.url,
      `${config.hostScheme}://${config.hostName}`,
    ).searchParams;
    const shop = query.get('shop')!;
```

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L178-194)
```typescript
    const authQuery: AuthQuery = Object.fromEntries(query.entries());
    if (!(await validQuery({config, query: authQuery, stateFromCookie}))) {
      log.error('Invalid OAuth callback', {shop, stateFromCookie});

      throw new ShopifyErrors.InvalidOAuthError('Invalid OAuth callback.');
    }

    log.debug('OAuth request is valid, requesting access token', {shop});

    const body = {
      client_id: config.apiKey,
      client_secret: config.apiSecretKey,
      code: query.get('code'),
      expiring: expiring ? '1' : '0',
    };

    const cleanShop = sanitizeShop(config)(query.get('shop')!, true)!;
```

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L51-58)
```typescript
function stringifyQueryForAdmin(query: AuthQuery): string {
  const processedQuery = new ProcessedQuery();
  Object.keys(query)
    .sort((val1, val2) => val1.localeCompare(val2))
    .forEach((key: string) => processedQuery.put(key, query[key]));

  return processedQuery.stringify(true);
}
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
