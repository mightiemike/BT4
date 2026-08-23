### Title
Duplicate-key parameter confusion in OAuth callback - HMAC validates last `shop`/`code` value while token exchange uses first value - ([File: packages/apps/shopify-api/lib/auth/oauth/oauth.ts])

### Finding Description
In `callback()` [1](#0-0) , the raw callback URL's query is parsed into a `URLSearchParams` object, and `shop` is obtained via `query.get('shop')` which, per the WHATWG spec, returns the **first** value when a key is repeated.

That same `query` object is then converted to a plain object with `Object.fromEntries(query.entries())` to build `authQuery` for HMAC/state validation [2](#0-1) . `Object.fromEntries` collapses duplicate keys by keeping the **last** occurrence, so if the query string contains `shop` (or `code`, `state`) twice, `authQuery.shop` holds the *last* value, which is what gets serialized by `stringifyQueryForAdmin` in `hmac-validator.ts` and is the value actually HMAC-validated [3](#0-2) .

Downstream, however, the code that performs the actual token exchange and session creation uses `query.get('code')` and `query.get('shop')` directly — i.e., the *first* occurrence [4](#0-3) . This is a genuine internal inconsistency: the value that is cryptographically authenticated (last duplicate) is not necessarily the value that is acted upon (first duplicate).

Additionally, `normalizeQuery` in `hmac-validator.ts` only rejects array-valued/duplicate `hmac`/`shop`/`signature`/`timestamp` for the `appProxy` signator via `APP_PROXY_SINGLE_VALUE_PARAMS` [5](#0-4) [6](#0-5) . For the `admin` signator, no such single-value enforcement exists at all — plain-object queries with array-valued or duplicated critical fields (`shop`, `code`) pass through unchecked.

### Impact Explanation
Practically, exploiting this to achieve cross-tenant token theft is constrained by Shopify's own OAuth semantics: the authorization `code` returned by Shopify's `/admin/oauth/authorize` flow is bound server-side to the specific shop domain that issued it, so redirecting the token-exchange POST to a different `shop` value than the one whose `code`/`hmac` were actually signed would simply cause Shopify's `/admin/oauth/access_token` endpoint to reject the exchange (`postResponse.ok` false) rather than yield a valid token for the wrong tenant. The confirmed, concrete impact is therefore a validation/consumption mismatch (parameter pollution) that can be used to make the app act on a different `shop`/`code` value than the one it just cryptographically verified, which is itself a defense-in-depth/authenticity-binding weakness even though I could not find a path in this codebase to a *successful* cross-tenant token or session compromise, since Shopify's backend still enforces the shop-code binding.

### Likelihood Explanation
This requires the attacker to deliver a callback URL to the app's `/auth/callback` route containing a duplicated `shop`/`code` parameter while a genuine, previously-signed `hmac`/`state`/`code` for one shop still validates (because it lands on the "last" value via `Object.fromEntries`) — e.g. via a phishing link sent directly to a merchant/browser, since the callback endpoint does not verify that the query it receives is byte-identical to what Shopify actually redirected. This is plausible without MITM (attacker fully controls the link they send), but requires the victim to click a crafted link and does not, by itself, defeat Shopify's server-side shop/code binding for full token theft.

### Recommendation
- In `normalizeQuery` (`hmac-validator.ts`), enforce single-value checks for `hmac`, `shop`, `signature`, `timestamp` (and ideally `code`, `state`) for **both** signators, not just `appProxy`, rejecting any plain-object or `URLSearchParams` query with duplicated critical keys.
- In `oauth.ts`'s `callback()`, derive `shop`/`code`/`state` from the exact same normalized/validated `authQuery` object used for HMAC validation (rather than re-reading via `query.get(...)` on the raw `URLSearchParams`), guaranteeing the validated value and the consumed value are always identical.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/auth/oauth/__tests__/oauth-duplicate-shop.test.ts
import {shopifyApi} from '../../..';
import {testConfig} from '../../../__tests__/test-config';

test('HMAC-validated shop differs from shop used for token exchange when shop is duplicated', () => {
  const shopify = shopifyApi(testConfig({apiSecretKey: 'secret'}));

  const url = new URL(
    'https://app.example.com/auth/callback?shop=attacker.myshopify.com&code=abc&state=xyz&timestamp=1700000000&shop=victim.myshopify.com&hmac=<hmac-signed-for-shop=victim.myshopify.com>',
  );

  const authQuery = Object.fromEntries(url.searchParams.entries());
  // Value used for HMAC validation ("last" wins):
  expect(authQuery.shop).toBe('victim.myshopify.com');

  // Value that oauth.ts callback() actually uses for token exchange ("first" wins):
  expect(url.searchParams.get('shop')).toBe('attacker.myshopify.com');

  // Assert they diverge -- this is the bug: the value bound by HMAC
  // is not the value acted upon.
  expect(authQuery.shop).not.toBe(url.searchParams.get('shop'));
});
```
Expected: the test demonstrates `authQuery.shop` (HMAC-validated) and `url.searchParams.get('shop')` (used for `sanitizeShop`/token exchange in `oauth.ts`) resolve to different values under a duplicated `shop` query parameter, confirming the authenticity-binding gap.

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

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L187-217)
```typescript
    const body = {
      client_id: config.apiKey,
      client_secret: config.apiSecretKey,
      code: query.get('code'),
      expiring: expiring ? '1' : '0',
    };

    const cleanShop = sanitizeShop(config)(query.get('shop')!, true)!;

    const postResponse = await fetchRequestFactory(config)(
      `https://${cleanShop}/admin/oauth/access_token`,
      {
        method: 'POST',
        body: JSON.stringify(body),
        headers: {
          'Content-Type': DataType.JSON,
          Accept: DataType.JSON,
        },
      },
    );

    if (!postResponse.ok) {
      throwFailedRequest(await postResponse.json(), false, postResponse);
    }

    const session: Session = createSession({
      accessTokenResponse: await postResponse.json<AccessTokenResponse>(),
      shop: cleanShop,
      state: stateFromCookie,
      config,
    });
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
