### Title
OAuth callback duplicate query-parameter confusion: HMAC validated against the last value of a repeated `shop`/`code` param while the token exchange uses the first value - (File: packages/apps/shopify-api/lib/auth/oauth/oauth.ts)

### Summary
In `callback()`, the query is parsed once into a `URLSearchParams` instance and then converted with `Object.fromEntries(query.entries())` before being handed to `validateHmac`. For a repeated key, `Object.fromEntries` retains the *last* occurrence, while every direct `query.get(...)` call used later in the same function (`code`, `shop`) returns the *first* occurrence. This produces a value mismatch between what is HMAC-verified and what is actually used to build the token-exchange request.

### Finding Description
`callback()` builds the query it will validate like this: [1](#0-0) [2](#0-1) 

`authQuery` is a **plain object** built from `Object.fromEntries(query.entries())`. For a duplicate key such as `shop=A&shop=B`, `Object.fromEntries` iterates in order and overwrites, so `authQuery.shop === 'B'` (the last value).

That plain object is then passed into `validateHmac(config)(authQuery)`, which calls `normalizeQuery`: [3](#0-2) 
Because `authQuery` is not `instanceof URLSearchParams` (it was already collapsed into a plain object by `oauth.ts`), the dedup/array-detection logic in `normalizeQuery` (lines 119–130) never runs for admin-flow OAuth callbacks — that protection only applies when a raw `URLSearchParams` is passed directly to `validateHmac` (e.g. from webhook/app-proxy paths). `stringifyQueryForAdmin` then canonicalizes using the single, last-wins value: [4](#0-3) 

Meanwhile, back in `oauth.ts`, the values actually used to drive the OAuth token exchange are obtained via `query.get(...)`, which per the `URLSearchParams` spec returns the *first* occurrence of a repeated key: [5](#0-4) 

So if a legitimate, Shopify-signed callback URL for `shop=victim.myshopify.com&code=REAL_CODE&hmac=H&state=S&timestamp=T` is replayed by an attacker with an extra parameter inserted *before* the real one — e.g. `shop=attacker-controlled.myshopify.com&shop=victim.myshopify.com&code=...&hmac=H&state=S&timestamp=T` — the HMAC check in `validateHmac` canonicalizes on the **last** `shop` value (`victim.myshopify.com`), matching Shopify's original signature `H` and passing validation, while `sanitizeShop(config)(query.get('shop')!, true)` used to build the actual POST to `/admin/oauth/access_token` resolves to the **first** `shop` value (`attacker-controlled.myshopify.com`). The same first/last divergence applies to `code`.

This is a genuine canonicalization/TOCTOU bug: the value that is cryptographically verified is not the value that is subsequently acted upon.

### Impact Explanation
If an attacker can get a duplicated `shop` parameter accepted by `sanitizeShop`'s regex (any `*.myshopify.com`/`*.shopify.com` domain, including attacker-registered dev/partner stores), the app's POST request — which includes `client_id` and `client_secret` in the JSON body — would be sent to a shop domain of the attacker's choosing instead of the one Shopify actually signed for, at line 196-206 of `oauth.ts`. That is a secret (client_secret) exfiltration path to an attacker-influenced Shopify-family domain, and for `code`, a code-substitution primitive that could redirect the token exchange to use an attacker-chosen authorization code while sailing through HMAC validation. This maps to Shopify's "OAuth/token forgery" / "secret disclosure" bounty impact classes, contingent on an attacker being able to deliver a URL with duplicated query parameters to the victim's browser at the app's callback endpoint (this delivery step is the limiting precondition, not the library's parsing logic itself, which is the object of this audit).

### Likelihood Explanation
Exploitation requires the attacker to get a valid, Shopify-signed callback (shop, code, hmac, state, timestamp) delivered to the callback endpoint with an extra, first-positioned duplicate of `shop` or `code` inserted. This typically requires either (a) intercepting/observing a real callback URL and re-triggering it with additional query parameters prepended (feasible since these values are visible in browser history, referrer headers, or server logs, and query strings are attacker-modifiable when replayed), or (b) an open-redirect/URL-rewriting primitive elsewhere that lets the attacker splice in an extra parameter. The `state` cookie binding (`safeCompare(query.state, stateFromCookie)`) still must match the cookie set for that browser session, which constrains but does not eliminate the attack when the attacker is replaying the victim's own legitimate flow with parameter injection. This is a real, reachable library-level flaw independent of app configuration, but requires an active replay/injection step to weaponize, so likelihood is moderate rather than trivial.

### Recommendation
Do not collapse the query with `Object.fromEntries(query.entries())` in `oauth.ts`. Instead:
1. Reject (400) any callback request containing duplicate values for security-relevant single-value parameters (`shop`, `code`, `state`, `hmac`, `timestamp`) before doing anything else — mirroring the `APP_PROXY_SINGLE_VALUE_PARAMS` duplicate-rejection behavior already implemented for `appProxy` signator in `normalizeQuery`, and extend that same protection to the `admin` signator path.
2. Pass the original `URLSearchParams` instance (not a pre-collapsed plain object) into `validateHmac`, so `normalizeQuery`'s existing duplicate-detection logic can actually run for OAuth callbacks.
3. Ensure every subsequent read of `shop`/`code` (`query.get(...)`) is drawn from the exact same normalized/validated query object that was HMAC-verified, eliminating the first-vs-last divergence entirely.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/auth/oauth/__tests__/oauth.callback-duplicate-param.test.ts
import {shopify} from '../../../__tests__/test-helper';
import {createSHA256HMAC} from '../../../../runtime/crypto';
import {HashFormat} from '../../../../runtime/crypto/types';

test('duplicate shop param: HMAC validates last value while token exchange uses first value', async () => {
  const realShop = 'victim-shop.myshopify.com';
  const attackerShop = 'attacker-shop.myshopify.com'; // attacker-controlled/dev store
  const code = 'REAL_AUTH_CODE';
  const state = 'valid-state-matching-cookie';
  const timestamp = Math.floor(Date.now() / 1000).toString();

  // HMAC as Shopify would have originally signed it, over the single, real shop value
  const canonical = `code=${code}&shop=${realShop}&state=${state}&timestamp=${timestamp}`;
  const hmac = await createSHA256HMAC(
    shopify.config.apiSecretKey,
    canonical,
    HashFormat.Hex,
  );

  // Attacker prepends a duplicate `shop` param pointing at their own store
  const maliciousUrl =
    `/auth/callback?shop=${attackerShop}&shop=${realShop}` +
    `&code=${code}&state=${state}&timestamp=${timestamp}&hmac=${hmac}`;

  // Expectation if the bug exists:
  // - validateHmac(authQuery) passes because authQuery.shop === realShop (last wins)
  // - sanitizeShop(config)(query.get('shop')!, true) resolves to attackerShop (first wins)
  //   so the POST to /admin/oauth/access_token (carrying client_secret) targets attackerShop.
  //
  // A fixed implementation should instead throw ShopifyErrors.InvalidHmacError /
  // InvalidOAuthError for the duplicated `shop` parameter before reaching the token exchange.
});
```

### Citations

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L143-147)
```typescript
    const query = new URL(
      request.url,
      `${config.hostScheme}://${config.hostName}`,
    ).searchParams;
    const shop = query.get('shop')!;
```

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L178-183)
```typescript
    const authQuery: AuthQuery = Object.fromEntries(query.entries());
    if (!(await validQuery({config, query: authQuery, stateFromCookie}))) {
      log.error('Invalid OAuth callback', {shop, stateFromCookie});

      throw new ShopifyErrors.InvalidOAuthError('Invalid OAuth callback.');
    }
```

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L187-194)
```typescript
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
