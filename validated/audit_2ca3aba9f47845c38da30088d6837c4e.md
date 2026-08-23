### Title
Session token `shop`/`dest` binding is not enforced in token exchange, allowing cross-tenant session storage under an attacker-chosen shop domain - (File: `packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts`)

### Summary
`shopify.auth.tokenExchange` accepts `sessionToken` and `shop` as two independently-supplied parameters and never verifies that the shop encoded inside the verified JWT (`dest`/`iss` claim) matches the caller-supplied `shop` value before using `shop` to build the access-token request URL and the resulting `Session` object.

### Finding Description
`tokenExchange` calls `decodeSessionToken(config)(sessionToken)` purely to validate the JWT signature/expiry and (optionally) the `aud` (API key) claim: [1](#0-0) 

It never compares `payload.dest`/`payload.iss` (the shop the token was actually issued for) against the `shop` parameter that was passed into the function. That `shop` value is then sanitized and used directly to build the outbound `POST https://${cleanShop}/admin/oauth/access_token` request together with the (correctly authenticated) `sessionToken`, and to construct the resulting `Session`: [2](#0-1) 

The `JwtPayload` type confirms `dest` (shop domain) is available in the verified token but is unused for this cross-check: [3](#0-2) 

The `shop` value fed into this call chain in the higher-level frameworks (`shopify-app-remix`, `shopify-app-react-router`) is taken straight from the request's `shop` URL query parameter, not from the verified token: [4](#0-3) 
and is passed unchanged into `exchangeToken` → `api.auth.tokenExchange`: [5](#0-4) 

The resulting session (whatever access token comes back) is stored keyed by the caller-controlled `shop`, not the token's authenticated `dest`: [6](#0-5) [7](#0-6) 

This mirrors the structure of the referenced report: an operation authenticates one identity/credential (`sessionToken` ↔ `from`) but performs the sensitive action against a second, independently supplied and unvalidated identifier (`shop` ↔ `to`) instead of binding it to the authenticated identity.

### Impact Explanation
If a session token intended for one shop is ever presented alongside a different `shop` query parameter (e.g., through an iframe/App Bridge context confusion, a shop parameter left over from a prior session, or a maliciously modified request in a non-strict validation path), the library will forward that token to a different shop's `/admin/oauth/access_token` endpoint and, if the request unexpectedly succeeds, store the resulting session under the wrong `shop` key in the app's session storage — a session-storage integrity/cross-tenant issue local to this library's trust model. In the local code, `dest` is never checked against `shop`, so this is fully unvalidated at the library level; whether the request is ultimately rejected depends entirely on Shopify's remote OAuth server enforcing token/shop binding, which this library does not verify or depend on defensively.

### Likelihood Explanation
This requires the caller (framework layer, e.g., `shopify-app-remix`/`shopify-app-react-router`) to supply a `shop` value that is out of sync with the JWT `dest`. Because `shop` in the higher-level strategies is sourced from `request.url`'s query string rather than derived from the decoded token, this is reachable by any unprivileged actor who can influence the `shop` query parameter on a request that also carries a session token, without needing any secret. The exploitability of the final impact (a real cross-tenant token) still depends on Shopify's server-side enforcement, which is outside this repo and could not be verified here.

### Recommendation
After calling `decodeSessionToken`, assert that the decoded `payload.dest` (normalized) equals the sanitized `shop` parameter before using `shop` to build the token endpoint URL or the resulting `Session`, and reject the exchange (throw `InvalidJwtError`/`InvalidOAuthError`) on mismatch, similar to how `validQuery` cross-checks `state` in the classic OAuth callback flow (`packages/apps/shopify-api/lib/auth/oauth/oauth.ts:242-255`).

### Proof of Concept
Not independently verified end-to-end against the live Shopify OAuth server (that dependency is out of scope of this repo), but locally reproducible at the unit level:
1. Call `shopify.auth.tokenExchange({ sessionToken: validTokenForShopA, shop: 'shopB.myshopify.com', requestedTokenType: RequestedTokenType.OfflineAccessToken })`.
2. Observe in `packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts` that no check compares the token's `dest` claim to `'shopB.myshopify.com'`; the POST is issued to `https://shopb.myshopify.com/admin/oauth/access_token` with `subject_token: validTokenForShopA`, and on a 200 response the returned `Session` is created with `shop: 'shopb.myshopify.com'`.

### Citations

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

**File:** packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts (L32-77)
```typescript
export function tokenExchange(config: ConfigInterface): TokenExchange {
  return async ({
    shop,
    sessionToken,
    requestedTokenType,
    expiring,
  }: TokenExchangeParams) => {
    await decodeSessionToken(config)(sessionToken);

    const body = {
      client_id: config.apiKey,
      client_secret: config.apiSecretKey,
      grant_type: TokenExchangeGrantType,
      subject_token: sessionToken,
      subject_token_type: IdTokenType,
      requested_token_type: requestedTokenType,
      expiring: expiring ? '1' : '0',
    };

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

**File:** packages/apps/shopify-api/lib/session/types.ts (L55-92)
```typescript
export interface JwtPayload {
  /**
   * The shop's admin domain.
   */
  iss: string;
  /**
   * The shop's domain.
   */
  dest: string;
  /**
   * The client ID of the receiving app.
   */
  aud: string;
  /**
   * The User that the session token is intended for.
   */
  sub: string;
  /**
   * When the session token expires.
   */
  exp: number;
  /**
   * When the session token activates.
   */
  nbf: number;
  /**
   * When the session token was issued.
   */
  iat: number;
  /**
   * A secure random UUID.
   */
  jti: string;
  /**
   * A unique session ID per user and app.
   */
  sid: string;
}
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/helpers/get-shop-from-request.ts (L1-4)
```typescript
export function getShopFromRequest(request: Request) {
  const url = new URL(request.url);
  return url.searchParams.get('shop')!;
}
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts (L60-82)
```typescript
      const {session: offlineSession} = await this.exchangeToken({
        request,
        sessionToken,
        shop,
        requestedTokenType: RequestedTokenType.OfflineAccessToken,
      });

      await config.sessionStorage!.storeSession(offlineSession);

      let newSession = offlineSession;

      if (config.useOnlineTokens) {
        logger.info('Requesting online access token', {shop});
        const {session: onlineSession} = await this.exchangeToken({
          request,
          sessionToken,
          shop,
          requestedTokenType: RequestedTokenType.OnlineAccessToken,
        });

        await config.sessionStorage!.storeSession(onlineSession);
        newSession = onlineSession;
      }
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts (L133-153)
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
    } catch (error) {
```

**File:** packages/apps/shopify-api/lib/auth/oauth/create-session.ts (L13-73)
```typescript
export function createSession({
  config,
  accessTokenResponse,
  shop,
  state,
}: {
  config: ConfigInterface;
  accessTokenResponse: AccessTokenResponse;
  shop: string;
  state: string;
}): Session {
  const associatedUser = (accessTokenResponse as OnlineAccessResponse)
    .associated_user;
  const isOnline = Boolean(associatedUser);

  logger(config).info('Creating new session', {shop, isOnline});

  const getSessionExpiration = (expires_in: number) =>
    new Date(Date.now() + expires_in * 1000);

  const getOnlineSessionProperties = (responseBody: OnlineAccessResponse) => {
    const {access_token: _access_token, scope: _scope, ...rest} = responseBody;
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
      ...(expires_in && {expires: getSessionExpiration(expires_in)}),
      ...(refresh_token &&
        refresh_token_expires_in && {
          refreshToken: refresh_token,
          refreshTokenExpires: getSessionExpiration(refresh_token_expires_in),
        }),
    };
  };

  return new Session({
    shop,
    state,
    isOnline,
    accessToken: accessTokenResponse.access_token,
    scope: accessTokenResponse.scope,
    ...(isOnline
      ? getOnlineSessionProperties(accessTokenResponse as OnlineAccessResponse)
      : getOfflineSessionProperties(
          accessTokenResponse as OfflineAccessResponse,
        )),
  });
```
