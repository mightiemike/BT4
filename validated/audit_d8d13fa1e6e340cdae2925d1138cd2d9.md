## Analysis Result

### Title
OAuth Callback Uses Inconsistent Duplicate-Parameter Resolution Between HMAC Validation and Shop Binding, Enabling Shop-Parameter Pollution - (File: `packages/apps/shopify-api/lib/auth/oauth/oauth.ts`)

### Summary
The `callback()` handler for the OAuth flow validates the request's HMAC/state against one interpretation of a possibly-duplicated `shop` query parameter, but then performs the actual token exchange and session binding using a *different* interpretation of the same duplicated parameter. This is the same root-cause pattern as the ERC20BalanceGteEnforcer report: a security check is performed against one instance of an address/value while the consequential action is carried out against a different, attacker-influenceable instance of that same nominal value.

### Finding Description
In `callback()`: [1](#0-0) 

`shop` (used for logging/bot checks) is read with `query.get('shop')`, which per the `URLSearchParams` spec returns the **first** occurrence of a repeated key.

Shortly after, the full query is converted with `Object.fromEntries(query.entries())`: [2](#0-1) 

`Object.fromEntries` on a duplicate-key iterator keeps the **last** occurrence, because each entry overwrites the previous one when building the plain object. This `authQuery` object — with "last-value" semantics for `shop` — is what gets passed into `validQuery`, which performs both the HMAC check and the state/nonce check: [3](#0-2) 

The HMAC is computed over `authQuery` via `generateLocalHmac`/`stringifyQueryForAdmin`, so the value of `shop` that is cryptographically bound by the HMAC check is the **last** duplicate.

However, immediately after this check passes, the code re-reads `shop` for the actual privileged action — the token-exchange target and session's shop — using `query.get('shop')` again (the **first** duplicate): [4](#0-3) 

The `validateHmac` implementation itself confirms this asymmetry: it only rejects duplicate `shop`/`hmac`/`signature`/`timestamp` values for the `appProxy` signator, not for the `admin` signator used by OAuth: [5](#0-4) 

The app-proxy path was explicitly hardened against this class of duplicate-parameter confusion (see the `'rejects a repeated security param'` and `'Throws a 400 response if the shop param appears more than once'` tests), but the equivalent protection was never applied to the admin/OAuth signator or to the two separate `shop` reads in `callback()`.

### Impact Explanation
Because the value that is cryptographically verified (`authQuery.shop`, last duplicate) can differ from the value that actually drives the token-exchange request and the resulting `Session.shop` (`query.get('shop')`, first duplicate), an attacker who controls the callback URL query string (e.g., by modifying a redirect/link before it reaches the app, since this is a plain unauthenticated GET request) can supply two different `shop` values. The HMAC/state check will validate against one shop while the code performs the token POST and creates a session bound to a different, attacker-chosen shop string. This breaks the intended guarantee that "a signed callback proves the request is legitimately for shop X," the same guarantee the ERC20 enforcer's `paymentAddress` fix is meant to restore by binding the check to the exact value acted upon.

### Likelihood Explanation
This requires an unauthenticated attacker to get a victim (or their own manipulated redirect) to hit `/auth/callback` with a duplicated `shop` query parameter — no secret knowledge is needed to create the duplicate, only control of the URL. The actual exploitability of forging a full account takeover further depends on Shopify's OAuth server enforcing (or not) that an authorization `code` is redeemable only against the exact shop domain it was issued for; that binding is outside this repository's scope and cannot be verified here. Regardless, the code contains a clear correctness/security defect: the HMAC-verified value and the value used for the privileged action are not guaranteed to be the same string.

### Recommendation
Resolve `shop` exactly once from the request and reuse that single, canonical value everywhere in `callback()`. Apply the same "reject queries where a security-relevant parameter appears more than once" hardening that already exists for the `appProxy` signator (`APP_PROXY_SINGLE_VALUE_PARAMS` in `packages/apps/shopify-api/lib/utils/hmac-validator.ts`) to the `admin` signator as well, so any duplicated `shop`, `hmac`, `state`, or `timestamp` parameter causes the callback to reject the request outright rather than silently picking different values for verification vs. action.

### Proof of Concept
1. Craft a callback URL: `/auth/callback?shop=attacker-target.myshopify.com&code=<code>&timestamp=<ts>&state=<state>&shop=legit-signed-shop.myshopify.com&hmac=<hmac valid for the query where shop's last value is legit-signed-shop.myshopify.com>`.
2. `query.get('shop')` (used for `cleanShop`/token exchange/session shop) returns `attacker-target.myshopify.com` (first occurrence).
3. `Object.fromEntries(query.entries())` used for HMAC/state validation resolves `shop` to `legit-signed-shop.myshopify.com` (last occurrence), matching the supplied `hmac`.
4. `validQuery` passes because the HMAC matches the last-value interpretation, but the subsequent access-token POST and `Session.shop` use the first-value interpretation — a value the HMAC never actually attested to.

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
