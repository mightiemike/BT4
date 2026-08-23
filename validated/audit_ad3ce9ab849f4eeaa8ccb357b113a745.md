### Title
Cross-tenant session forgery via unchecked `shop` parameter in `tokenExchange()` - (File: `packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts`)

### Summary
`tokenExchange()` decodes and verifies the JWT `sessionToken` signature but discards the decoded payload without ever comparing `payload.dest`/`payload.iss` to the caller-supplied `shop` argument. Because `shop` is taken from the unauthenticated `?shop=` query parameter (via `getShopFromRequest`) independently of the token's `dest` claim, an attacker holding a valid session token for their own shop can request an access-token exchange against an arbitrary victim shop domain.

### Finding Description
In `tokenExchange()` [1](#0-0)  the function calls `await decodeSessionToken(config)(sessionToken)` purely for its side effect of verifying the HMAC signature and expiry/audience, but the returned `JwtPayload` (which contains `dest`/`iss`, i.e. the shop the token was actually issued for) is never captured or checked. `decodeSessionToken` itself only validates signature, expiry, and (optionally) `aud` — it never checks `dest` against anything [2](#0-1) .

The `shop` used for the actual token exchange call and for `createSession()` comes solely from the function argument, sanitized only for format via `sanitizeShop`, not cross-checked against the token: [3](#0-2) .

In the shopify-app-remix integration, this `shop` value originates from the raw, attacker-controlled query string: `getShopFromRequest` simply returns `url.searchParams.get('shop')` [4](#0-3) , and `validateSessionToken` decodes the token but likewise never compares its `dest` to `getShopFromRequest(request)` [5](#0-4) . `TokenExchangeStrategy.exchangeToken` then forwards this same disjoint `(shop, sessionToken)` pair straight into `api.auth.tokenExchange()` [6](#0-5) .

Shopify's own `/admin/oauth/access_token` endpoint is contacted at `https://${cleanShop}/admin/oauth/access_token` with the attacker's real `subject_token` (their own valid session token). Since Shopify's token-exchange endpoint issues tokens keyed to the shop domain in the URL combined with the app's own `client_id`/`client_secret` (which the app always sends, since these are the app's own credentials, not shop-specific secrets), a request to the victim shop's endpoint using a subject token minted for shop-A would be evaluated by Shopify's backend, not this library — but the critical library-side defect is that this app-side code will happily attempt (and act on) a cross-shop association without ever detecting or rejecting the mismatch itself before calling `createSession()`. If the exchange succeeds (e.g., because the token-exchange endpoint on Shopify's side is lenient, or because a race/config allows it), `createSession()` builds a `Session` keyed by `cleanShop` (attacker-controlled) and computed session IDs via `getJwtSessionId`/`getOfflineId` using that same attacker-controlled shop [7](#0-6) , which the caller then persists via `storeSession()`.

Whether Shopify's remote OAuth endpoint itself enforces subject-token-to-shop binding is outside of this repository's control, but the library provides no defense-in-depth check of its own — it is fully reliant on the remote endpoint to catch a mismatch. This is a genuine gap relative to the stated invariant, since none of `sanitizeShop`, `decodeSessionToken`, or `tokenExchange` cross-validates `dest`/`iss` against the `shop` parameter.

### Impact Explanation
If exploitable, an attacker could cause the app to associate an access token/session with a shop domain of their choosing rather than the one their session token actually authenticates for, leading to cross-tenant session/storage confusion (attempted access-token acquisition or session-store poisoning for `victim.myshopify.com` using an attacker-controlled request). This matches Shopify's "cross-tenant data access" bounty impact class.

### Likelihood Explanation
The attacker only needs a legitimate session token for their own installed shop (trivially obtainable, e.g., from their own embedded app iframe) and the ability to send a crafted request with a different `shop` query parameter. No secrets, privileged roles, or MITM are required — this is fully within the "unprivileged merchant" threat model. However, actual exploitability is capped by whether Shopify's `/admin/oauth/access_token` endpoint validates the subject token's `dest` against the request shop domain server-side; this repository cannot confirm or deny that remote behavior, so the library-level defect is confirmed but the end-to-end exploitability depends on an external service not present in this codebase.

### Recommendation
In `tokenExchange()`, capture the decoded payload and validate `payload.dest` (or `iss`) against the sanitized `shop` before proceeding, throwing `InvalidJwtError` on mismatch — e.g.:
```ts
const payload = await decodeSessionToken(config)(sessionToken);
const cleanShop = sanitizeShop(config)(shop, true)!;
if (payload.dest.replace(/^https:\/\//, '') !== cleanShop) {
  throw new ShopifyErrors.InvalidJwtError('Session token shop does not match requested shop');
}
```
Apply the equivalent check in `validateSessionToken` in shopify-app-remix/react-router before using `shop` from the query string.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/auth/oauth/__tests__/token-exchange.test.ts
it('does not validate shop against session token dest claim', async () => {
  const shopA = 'shop-a.myshopify.com';
  const shopVictim = 'victim.myshopify.com';

  // sessionToken is a validly signed JWT with dest/iss = shopA
  const sessionToken = await createValidJwt({dest: `https://${shopA}/`, aud: shopify.config.apiKey});

  fetchMock.mockResponse(JSON.stringify({access_token: 'attacker-token', scope: 'read_products'}));

  const {session} = await shopify.auth.tokenExchange({
    shop: shopVictim, // attacker-supplied, mismatched with token's dest
    sessionToken,
    requestedTokenType: RequestedTokenType.OfflineAccessToken,
  });

  // Vulnerable: no exception thrown, and session.shop === shopVictim
  // despite sessionToken having been issued for shopA.
  expect(session.shop).toBe(shopVictim);
});
```
Expected secure behavior: this call should throw `InvalidJwtError` because `sessionToken`'s `dest` (`shopA`) does not match the requested `shop` (`victim.myshopify.com`); currently it does not.

### Citations

**File:** packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts (L32-39)
```typescript
export function tokenExchange(config: ConfigInterface): TokenExchange {
  return async ({
    shop,
    sessionToken,
    requestedTokenType,
    expiring,
  }: TokenExchangeParams) => {
    await decodeSessionToken(config)(sessionToken);
```

**File:** packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts (L51-77)
```typescript
    const cleanShop = sanitizeShop(config)(shop, true)!;

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

    return {
      session: createSession({
        accessTokenResponse: await postResponse.json<AccessTokenResponse>(),
        shop: cleanShop,
        // We need to keep this as an empty string as our template DB schemas have this required
        state: '',
        config,
      }),
    };
```

**File:** packages/apps/shopify-api/lib/session/decode-session-token.ts (L15-43)
```typescript
export function decodeSessionToken(config: ConfigInterface) {
  return async (
    token: string,
    {checkAudience = true}: DecodeSessionTokenOptions = {},
  ): Promise<JwtPayload> => {
    let payload: JwtPayload;
    try {
      payload = (
        await jose.jwtVerify(token, getHMACKey(config.apiSecretKey), {
          algorithms: ['HS256'],
          clockTolerance: JWT_PERMITTED_CLOCK_TOLERANCE,
        })
      ).payload as unknown as JwtPayload;
    } catch (error) {
      throw new ShopifyErrors.InvalidJwtError(
        `Failed to parse session token '${token}': ${error.message}`,
      );
    }

    // The exp and nbf fields are validated by the JWT library

    if (checkAudience && payload.aud !== config.apiKey) {
      throw new ShopifyErrors.InvalidJwtError(
        'Session token had invalid API key',
      );
    }

    return payload;
  };
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/helpers/get-shop-from-request.ts (L1-4)
```typescript
export function getShopFromRequest(request: Request) {
  const url = new URL(request.url);
  return url.searchParams.get('shop')!;
}
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/helpers/validate-session-token.ts (L13-39)
```typescript
export async function validateSessionToken(
  params: BasicParams,
  request: Request,
  token: string,
  {checkAudience = true, retryRequest = true}: ValidateSessionTokenOptions = {},
): Promise<JwtPayload> {
  const {api, logger} = params;
  const shop = getShopFromRequest(request);
  logger.debug('Validating session token', {shop});

  try {
    const payload = await api.session.decodeSessionToken(token, {
      checkAudience,
    });
    logger.debug('Session token is valid - validated', {
      shop,
      payload: JSON.stringify(payload),
    });

    return payload;
  } catch (error) {
    logger.debug(`Failed to validate session token: ${error.message}`, {
      shop,
    });

    throw respondToInvalidSessionToken({params, request, retryRequest});
  }
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts (L133-152)
```typescript
  private async exchangeToken({
    request,
    shop,
    sessionToken,
    requestedTokenType,
  }: {
    request: Request;
    shop: string;
    sessionToken: string;
    requestedTokenType: RequestedTokenType;
  }): Promise<{session: Session}> {
    const {api, config, logger} = this;

    try {
      return await api.auth.tokenExchange({
        sessionToken,
        shop,
        requestedTokenType,
        expiring: config.future.expiringOfflineAccessTokens,
      });
```

**File:** packages/apps/shopify-api/lib/auth/oauth/create-session.ts (L35-52)
```typescript
    const sessionId = config.isEmbeddedApp
      ? getJwtSessionId(config)(
          shop,
          `${(rest as OnlineAccessInfo).associated_user.id}`,
        )
      : crypto.randomUUID();

    return {
      id: sessionId,
      onlineAccessInfo: rest,
      expires: getSessionExpiration(rest.expires_in),
    };
  };

  const getOfflineSessionProperties = (responseBody: OfflineAccessResponse) => {
    const {expires_in, refresh_token, refresh_token_expires_in} = responseBody;
    return {
      id: getOfflineId(config)(shop),
```
