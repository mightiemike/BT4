`getSessionTokenFromUrlParam` in `helpers/get-session-token-header.ts` is merely a URL-parameter extractor — it does not decode or verify the JWT itself. Actual verification happens downstream via `validateSessionToken`, which calls `api.session.decodeSessionToken`, which in turn uses `jose.jwtVerify` with `algorithms: ['HS256']` and the app secret, and `jose` enforces `exp`/`nbf` claims as part of verification. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/helpers/get-session-token-header.ts (L7-11)
```typescript
export function getSessionTokenFromUrlParam(request: Request): string | null {
  const url = new URL(request.url);

  return url.searchParams.get(SESSION_TOKEN_PARAM);
}
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts (L195-217)
```typescript
  const headerSessionToken = getSessionTokenHeader(request);
  const searchParamSessionToken = getSessionTokenFromUrlParam(request);
  const sessionToken = (headerSessionToken || searchParamSessionToken)!;

  logger.debug('Attempting to authenticate session token', {
    shop: getShopFromRequest(request),
    sessionToken: JSON.stringify({
      header: headerSessionToken,
      search: searchParamSessionToken,
    }),
  });

  if (config.isEmbeddedApp) {
    const payload = await validateSessionToken(params, request, sessionToken);
    const dest = new URL(payload.dest);
    const shop = dest.hostname;

    logger.debug('Session token is valid - authenticated', {shop, payload});
    const sessionId = config.useOnlineTokens
      ? api.session.getJwtSessionId(shop, payload.sub)
      : api.session.getOfflineId(shop);

    return {shop, payload, sessionId, sessionToken};
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
