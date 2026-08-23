## Title
OAuth callback shop/code duplicate-parameter confusion allows the HMAC-validated query to diverge from the query actually used for token exchange - (File: `packages/apps/shopify-api/lib/auth/oauth/oauth.ts`)

### Summary
The external report describes a bug class where a critical identifier (`scriptPubKeyHash`) is never checked for duplicates/collisions, so different code paths can end up trusting different instances of that identifier for validation versus for the actual state-changing action. The `shopify-api` OAuth callback handler has the same class of defect: it derives the `shop` (and `code`) value used for the security check (HMAC/state validation) from a *different* duplicate-parameter resolution than the `shop` value it actually uses to perform the token exchange and create the session.

### Finding Description
In `callback()`, the query is parsed twice with two different duplicate-key semantics: [1](#0-0) 

`query.get('shop')` uses `URLSearchParams.get()`, which returns the **first** occurrence of a repeated key.

A few lines later, the object used for HMAC/state validation is built with `Object.fromEntries(query.entries())`, which — for repeated keys — silently keeps the **last** occurrence, overwriting earlier ones: [2](#0-1) 

That collapsed object (`authQuery`) is then passed into `validQuery`/`validateHmac`: [3](#0-2) 

Because `authQuery` at this point is a plain object (not a `URLSearchParams` instance) and the signator defaults to `'admin'`, `normalizeQuery` takes the early-return branch and performs **no duplicate-parameter detection at all** for the admin/OAuth-callback path — the app-proxy hardening (`APP_PROXY_SINGLE_VALUE_PARAMS` check) added in a separate fix only applies when `signator === 'appProxy'` and only inspects arrays, which can never occur here since duplicates were already collapsed by `Object.fromEntries` before reaching `validateHmac`: [4](#0-3) 

Finally, after the (potentially mismatched) validation succeeds, the code re-reads `query.get('shop')` (first occurrence again) to build the `cleanShop` used for the actual token-exchange request and session creation: [5](#0-4) 

So a callback URL with a repeated `shop` (or `code`) parameter can be HMAC-validated against the **last** value while the token exchange/session creation is performed against the **first** value — exactly the "no duplicate check, multiple code paths pick different instances of the same identifier" bug class described in the report, here applied to the OAuth `shop`/`code` parameters instead of `scriptPubKeyHash`.

### Impact Explanation
This breaks the intended cryptographic guarantee that "the shop/code that Shopify signed is the shop/code the app acts on." An attacker who possesses one legitimately-signed OAuth callback (e.g., from installing the app on their own shop) can inject an additional duplicate `shop`/`code` parameter so that:
- HMAC validation is performed against the value Shopify actually signed (passes), while
- the token-exchange endpoint and resulting `Session.shop` are built from a different, attacker-chosen value.

This can misdirect where the access-token exchange request is sent and which shop domain the resulting session is stored/labeled under, undermining the integrity checks that this code path is supposed to provide. It affects any unauthenticated caller who can trigger `/auth/callback` with a crafted query string.

### Likelihood Explanation
Likelihood is Low/Medium: exploitation requires the attacker to hold a genuine Shopify-signed OAuth callback (obtainable by installing the app on any shop they control) and to have or induce a matching `state` cookie context, and ultimate impact is bounded by Shopify's own server-side validation of `code` against the shop domain in the token-exchange URL. Still, the internal validated-value/used-value mismatch is a concrete, reachable defect in the library's own logic, independent of whether Shopify's backend fully blocks the resulting cross-shop token exchange.

### Recommendation
Parse the OAuth callback query exactly once, reject (rather than silently collapse) any repeated occurrence of security-critical parameters (`shop`, `code`, `state`, `hmac`, `timestamp`), and ensure the single canonical value is used consistently for both HMAC/state validation and for the token-exchange request/session creation — mirroring the `APP_PROXY_SINGLE_VALUE_PARAMS` duplicate-rejection already applied to the app-proxy signator, but applied unconditionally (including the `'admin'` signator) and before any `Object.fromEntries`/`URLSearchParams.get()` divergence can occur.

### Proof of Concept
1. Attacker installs the app on `attacker-shop.myshopify.com`, obtaining a genuine Shopify-signed callback URL:
   `GET /auth/callback?code=CODE&hmac=HMAC&shop=attacker-shop.myshopify.com&state=STATE&timestamp=TS`
2. Attacker crafts a modified URL by prepending a duplicate `shop` parameter:
   `GET /auth/callback?code=CODE&hmac=HMAC&shop=victim-shop.myshopify.com&shop=attacker-shop.myshopify.com&state=STATE&timestamp=TS`
3. In `callback()`, `query.get('shop')` returns `victim-shop.myshopify.com` (first value) — used later for `cleanShop`/token exchange/session.
4. `Object.fromEntries(query.entries())` collapses the duplicate to `attacker-shop.myshopify.com` (last value) — this is what gets HMAC-validated in `validQuery`, and it matches `HMAC` since that's what Shopify actually signed.
5. Validation passes, yet the code proceeds to call `https://victim-shop.myshopify.com/admin/oauth/access_token` and creates/labels the resulting `Session` with `shop: victim-shop.myshopify.com`, a value that was never actually covered by the verified HMAC.

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
