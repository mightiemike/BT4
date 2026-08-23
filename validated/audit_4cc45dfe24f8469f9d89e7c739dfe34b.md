### Title
OAuth callback shop-confusion via duplicate `shop` query parameters causes HMAC validation and access-token exchange to bind to different shops - (File: `packages/apps/shopify-api/lib/auth/oauth/oauth.ts`)

### Summary
In the OAuth `callback()` handler, the HMAC-validated shop value and the shop used for the access-token POST are derived from two different parsing methods that disagree on duplicate `shop` query parameters. `Object.fromEntries(query.entries())` keeps the **last** occurrence of a repeated key, while `URLSearchParams.get('shop')` returns the **first** occurrence, letting an attacker who crafts a callback URL with two `shop` params make the HMAC check validate against one shop while the token exchange is performed against another.

### Finding Description
`callback()` builds two separate representations of the same query string: [1](#0-0) 
`shop` here comes from `query.get('shop')`, which per the URLSearchParams spec returns the value of the **first** `shop=` pair in the string. [2](#0-1) 
`authQuery` is built with `Object.fromEntries(query.entries())`. When `entries()` yields multiple pairs for the same key, `Object.fromEntries` performs successive property assignment, so the **last** occurrence overwrites earlier ones and becomes `authQuery.shop`.

`validQuery` -> `validateHmac` -> `generateLocalHmac` -> `stringifyQueryForAdmin` computes the local HMAC entirely from `authQuery` (last-value-wins for `shop`): [3](#0-2) [4](#0-3) 

Crucially, `normalizeQuery` in `hmac-validator.ts` only rejects duplicate/array-valued fields when `query instanceof URLSearchParams` **and only for the `appProxy` signator**; for the plain-object `authQuery` passed by `validQuery` (admin signator), no such array/duplicate check is performed at all: [5](#0-4) 

After HMAC/state validation succeeds, the code re-reads `shop` directly from the raw `URLSearchParams`, which returns the **first** value, and uses it for the actual token exchange: [6](#0-5) 

So for a URL of the form `?shop=attacker.myshopify.com&shop=victim.myshopify.com&code=...&state=...&hmac=...` (where `code`/`hmac`/`state` are the genuine values Shopify issued for `victim.myshopify.com`'s real OAuth authorization):
- `authQuery.shop` = `victim.myshopify.com` (last-wins) → HMAC recomputation matches Shopify's real signature exactly as if the extra duplicate weren't there, so `validQuery` returns `true`.
- `query.get('shop')` = `attacker.myshopify.com` (first-wins) → `cleanShop` used for the `POST https://<cleanShop>/admin/oauth/access_token` request (which carries `client_secret` and the victim's `code`) targets the attacker-controlled shop domain instead of the shop that was actually HMAC-validated.

`sanitizeShop` restricts the destination to a `*.myshopify.com` (or configured) suffix, so this cannot be redirected to an arbitrary attacker server/SSRF, but it does break the authenticity binding between the HMAC-validated shop and the shop that receives the token-exchange call, i.e., exactly the invariant the question asks about.

### Impact Explanation
This is a shop-confusion / broken authenticity-binding bug in the OAuth callback: the shop value that passed the cryptographic HMAC check is not the shop the app subsequently performs the access-token exchange against and later creates a `Session` for (`cleanShop` also becomes `session.shop`, see `createSession` call). This falls under Shopify's "OAuth CSRF / access-token theft / cross-tenant confusion" impact class, since it lets an attacker cause the app to exchange another shop's authorization code against a destination of the attacker's choosing (within the myshopify.com domain space) and to create/attribute a session under the wrong shop identity.

### Likelihood Explanation
Exploitation requires the attacker to obtain a genuine callback URL (containing a real, still-valid `code`, `state`, and `hmac`) — e.g., by directing a victim merchant to click a maliciously modified version of the real installation-callback link, or by intercepting/replaying it before the code is consumed. The attacker only needs to insert one duplicate `shop=` parameter; no secret, elevated privilege, or non-default configuration is needed. Practical impact is bounded by whether Shopify's own `/admin/oauth/access_token` endpoint enforces code-to-shop binding server-side, but the library-level authenticity check itself is bypassed as described.

### Recommendation
Parse the callback query exactly once into a single canonical representation and use that same object/value for both HMAC validation and the token-exchange request — do not re-derive `shop` from `query.get()` after already extracting `authQuery`. Additionally, `normalizeQuery`/`validQuery` should explicitly reject any duplicate/array-valued security-critical parameters (`shop`, `hmac`, `state`, `code`) for the `admin` signator, mirroring the existing `APP_PROXY_SINGLE_VALUE_PARAMS` protection currently limited to `appProxy`.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/auth/oauth/__tests__/oauth.test.ts
import {callback} from '../oauth';
import {generateLocalHmac} from '../../../utils/hmac-validator';

test('duplicate shop params diverge between HMAC validation and token exchange', async () => {
  const state = 'test-state';
  const legitimateShop = 'victim-shop.myshopify.com';
  const attackerShop = 'attacker-shop.myshopify.com';

  const hmac = await generateLocalHmac(config)({
    shop: legitimateShop, // hmac computed as if only the last (victim) shop existed
    state,
    code: 'real-code',
    timestamp: `${Math.floor(Date.now() / 1000)}`,
  });

  // Duplicate shop param: attacker first, victim second
  const url =
    `/callback?shop=${attackerShop}&shop=${legitimateShop}` +
    `&state=${state}&code=real-code&hmac=${hmac}` +
    `&timestamp=${Math.floor(Date.now() / 1000)}`;

  // ... set up the STATE_COOKIE_NAME signed cookie for `state`, mock fetchRequestFactory ...

  await callback(config)({rawRequest: makeRequest(url), rawResponse: makeResponse()});

  // Assert: validQuery() returned true (HMAC matched victim shop),
  // but the POST to /admin/oauth/access_token was sent to attackerShop,
  // not legitimateShop -- i.e. cleanShop !== authQuery.shop used for HMAC.
  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining(attackerShop), // divergent target
    expect.anything(),
  );
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

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L187-206)
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

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L51-82)
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
