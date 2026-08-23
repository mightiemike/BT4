This confirms the intermediate path derives `shop` from the decoded token itself, not from separate user-controlled input, at least in the shopify-app-express and shopify-app-remix/react-router host wrappers.### Title
Missing dest/shop binding check in `tokenExchange` allows cross-tenant token-exchange request forgery - (File: packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts)

### Summary
`tokenExchange` decodes and cryptographically verifies the caller-supplied `sessionToken` but never compares its `dest`/shop claim against the separately supplied `shop` parameter before issuing the `/admin/oauth/access_token` request and storing the resulting session under `shop`. The library's own documented integration pattern feeds `shop` from an untrusted `req.query.shop` value, independent of the session token, making a shop-confusion request reachable from application code that follows the official example.

### Finding Description
In `tokenExchange` [1](#0-0) , the function calls `decodeSessionToken(config)(sessionToken)` purely to validate the JWT signature/claims (`exp`, `nbf`, `aud`) but discards the returned payload — it never reads `payload.dest` to confirm it matches the `shop` argument. The subsequent request is built entirely from the caller-supplied `shop`: [2](#0-1) , and the resulting session is persisted keyed to `cleanShop`, not the shop encoded in the token: [3](#0-2) .

`decodeSessionToken` itself only validates signature, expiry, and (optionally) audience — it never asserts anything about `dest`: [4](#0-3) .

Critically, the library's own reference documentation demonstrates exactly the vulnerable calling pattern, deriving `shop` from an untrusted query parameter independently of the session token: [5](#0-4) .

By contrast, the maintained host-framework wrappers (`shopify-app-express`, `shopify-app-remix`, `shopify-app-react-router`) do not follow this documented pattern — they derive `shop` from the verified token's own `dest` claim before calling `tokenExchange`, e.g. `const shop = new URL(payload.dest).hostname;` [6](#0-5) , and similarly in the Remix/React Router strategies via `getSessionTokenContext`, which sets `shop` from `new URL(payload.dest).hostname` [7](#0-6) . This means in the shipped host integrations the mismatch is not reachable — but any developer building directly on `shopify.auth.tokenExchange` per the official docs (or any future/alternate host integration) inherits the flaw, and the library provides no defense-in-depth check to prevent it.

### Impact Explanation
If a caller (host app or an app built directly on `shopify-api`) passes an attacker-controlled `shop` parameter alongside a valid-but-unrelated `sessionToken`, `tokenExchange` will issue an OAuth token-exchange request naming shop B while presenting a subject_token scoped to shop A, and (if Shopify's OAuth backend does not itself reject the shop/subject mismatch) will store the returned access token under shop B's session ID — a cross-tenant offline/online token issuance and session-storage confusion. This maps to a cross-tenant data/session access impact class. Whether Shopify's server-side `/admin/oauth/access_token` endpoint enforces host-vs-subject binding cannot be verified from this repository; that half of the precondition is external to the codebase.

### Likelihood Explanation
The precondition requires a caller of `tokenExchange` to supply `shop` from an untrusted source independently of the verified session token's `dest` — exactly the pattern shown in the library's own `tokenExchange.md` example using `req.query.shop`. The maintained host packages in this monorepo do not exhibit this pattern (they bind `shop` to `payload.dest`), so likelihood against those specific packages is low/not reachable. Likelihood is higher for any app or third-party integration that follows the documented example literally, or for future host integrations that don't replicate the `dest`-derivation defense.

### Recommendation
Have `tokenExchange` retain the decoded payload and assert that `new URL(payload.dest).hostname` (case/format normalized) equals the sanitized `shop` argument before constructing the request, throwing an `InvalidJwtError`/similar on mismatch. Additionally, update `tokenExchange.md` to derive `shop` from the verified token's `dest` claim rather than from `req.query.shop`, consistent with how the host-framework wrappers already behave.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/auth/oauth/__tests__/token-exchange.test.ts (illustrative addition)
it('does not verify that shop matches the session token dest before requesting a token', async () => {
  const shopA = 'shop-a.myshopify.com';
  const shopB = 'shop-b.myshopify.com';

  const tokenForShopA = await signJWT(shopify.config.apiSecretKey, {
    iss: `https://${shopA}/admin`,
    dest: `https://${shopA}`,
    aud: shopify.config.apiKey,
    sub: '1',
    exp: Date.now() / 1000 + 3600,
    nbf: 1234,
    iat: 1234,
    jti: '1234',
    sid: 'abc',
  });

  queueMockResponse(JSON.stringify(successResponse)); // mocked /admin/oauth/access_token

  const {session} = await shopify.auth.tokenExchange({
    shop: shopB,               // attacker-supplied, unrelated to token
    sessionToken: tokenForShopA,
    requestedTokenType: RequestedTokenType.OfflineAccessToken,
  });

  // BUG: request is sent to shop B's endpoint using shop A's session token,
  // and the resulting session is stored under shop B.
  expect(session.shop).toBe(shopB);
  expect(fetchMock).toHaveBeenCalledWith(
    expect.objectContaining({url: `https://${shopB}/admin/oauth/access_token`}),
  );
});
```
No assertion in `tokenExchange` compares `dest` (shop A) against the `shop` parameter (shop B) prior to this request, confirming the missing binding check described above. [8](#0-7)

### Citations

**File:** packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts (L32-63)
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
```

**File:** packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts (L69-77)
```typescript
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

**File:** packages/apps/shopify-api/docs/reference/auth/tokenExchange.md (L14-27)
```markdown
```ts
app.get('/auth', async (req, res) => {
  const shop = shopify.utils.sanitizeShop(req.query.shop, true);
  const headerSessionToken = getSessionTokenHeader(request);
  const searchParamSessionToken = getSessionTokenFromUrlParam(request);
  const sessionToken = (headerSessionToken || searchParamSessionToken)!;

  await shopify.auth.tokenExchange({
    sessionToken,
    shop,
    requestedTokenType: RequestedTokenType.OfflineAccessToken, // or RequestedTokenType.OnlineAccessToken
    expiring: true, // Optional, defaults to false
  });
});
```

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L66-72)
```typescript
    const payload = await api.session.decodeSessionToken(sessionToken);
    const shop = new URL(payload.dest).hostname;
    const sub = payload.sub;

    const sessionId = config.useOnlineTokens
      ? api.session.getJwtSessionId(shop, sub)
      : api.session.getOfflineId(shop);
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts (L207-217)
```typescript
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
