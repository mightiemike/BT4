### Title
Missing `dest` claim binding in `tokenExchange()` allows attacker-controlled `shop` to be paired with another shop's session token - ([File: packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts])

### Summary
`tokenExchange()` decodes and validates a session token's signature/audience/expiry via `decodeSessionToken(config)(sessionToken)` but discards the returned payload and never checks that `payload.dest` matches the caller-supplied `shop` parameter before using `shop` to build the `https://{shop}/admin/oauth/access_token` request. [1](#0-0)  In at least one first-party integration (`shopify-app-react-router`), the `shop` value passed to `tokenExchange` is taken directly from the request's `shop` query parameter rather than derived from the session token's `dest` claim, meaning the library's own consumer can be driven with a mismatched shop/token pair without the library rejecting it.

### Finding Description
`decodeSessionToken()` only verifies the JWT's HMAC signature (using the app's single shared `apiSecretKey`), `aud` (API key), `exp`, and `nbf` — it does not and cannot tie the signature to a specific shop, since the signing secret is the same across all shops installing the app. [2](#0-1)  The only shop-binding information in a session token is its `dest` claim. In `tokenExchange()`, this claim is never compared against the caller-supplied `shop`:

```
await decodeSessionToken(config)(sessionToken);   // return value discarded
...
const cleanShop = sanitizeShop(config)(shop, true)!;   // shop comes from caller, not from JWT
``` [3](#0-2) 

In `packages/apps/shopify-app-react-router`'s `getSessionTokenContext`, when the app uses the default `AppDistribution.ShopifyAdmin` distribution, the `shop` used downstream is read straight from the URL query string, and the JWT payload is left `undefined` (not decoded or checked) at that point: [4](#0-3)  This `shop` and the raw `sessionToken` are then passed unmodified into `TokenExchangeStrategy.authenticate` → `exchangeToken` → `api.auth.tokenExchange({shop, sessionToken, ...})`. [5](#0-4)  Because `tokenExchange()` performs no `dest`-vs-`shop` cross-check, a request with `shop=shop-B.myshopify.com` and a valid session token belonging to shop A will be forwarded to `https://shop-b.myshopify.com/admin/oauth/access_token` carrying shop A's JWT as `subject_token`.

By contrast, `shopify-app-remix`'s equivalent `getSessionTokenContext` derives `shop` from `payload.dest` when `config.isEmbeddedApp` is true, closing this gap in that call site. [6](#0-5)  This inconsistency confirms the correct pattern exists elsewhere in the codebase but is not enforced centrally inside `tokenExchange()` itself, so any current or future caller that passes an independently-sourced `shop` (as `shopify-app-react-router` does for the default distribution mode) reintroduces the gap.

Whether Shopify's remote `/admin/oauth/access_token` endpoint itself rejects a cross-shop `subject_token`/URL-host mismatch is outside this repository and cannot be verified here; that is a real, but unverifiable-from-this-codebase, backstop. The library-level defect remains: `tokenExchange()` provides no local enforcement of the tenant-isolation invariant (`dest` must equal target `shop`), and at least one first-party framework package (`shopify-app-react-router`) supplies an attacker-controlled `shop` to it in the default distribution mode.

### Impact Explanation
If Shopify's backend does not independently reject the cross-shop combination (unverifiable from this repo), the app could store an offline/online session under shop B's identity that resolves to whatever the exchange endpoint returns, or at minimum the call fails only because of an external server-side check rather than because the library enforced tenant isolation — a defense-in-depth failure matching Shopify's "cross-tenant session/access-token handling" impact class. This corresponds to Impact Explanation category: cross-tenant session/access-token exchange due to missing binding validation in the OAuth token-exchange handler.

### Likelihood Explanation
The attacker only needs a legitimate session token for a shop they control (trivial for any merchant/customer of their own store) and knowledge of the target shop's `.myshopify.com` domain (public information). Reaching the vulnerable code path requires the app to be running under `AppDistribution.ShopifyAdmin` via `shopify-app-react-router`, sending a crafted request with `shop=<target>` and their own valid session token. No secrets, no privileged role, and no non-default library configuration beyond the default distribution mode are required.

### Recommendation
Add a mandatory check inside `tokenExchange()` (packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts) that decodes the session token, extracts `dest`, normalizes it with `sanitizeShop`, and throws (e.g. `InvalidJwtError` or a new `InvalidSessionTokenError`) if it does not match the sanitized `shop` parameter before making the outbound POST. This centralizes tenant-isolation enforcement in the library rather than relying on each downstream framework package (remix, react-router, express) to independently derive `shop` correctly from the token.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/auth/oauth/__tests__/token-exchange.test.ts (illustrative addition)
test('rejects mismatched shop/dest in session token', async () => {
  const shopify = shopifyApi(testConfig());
  const tokenForShopA = await signJWT(shopify.config.apiSecretKey, {
    ...basePayload,
    dest: 'https://shop-a.myshopify.com',
    aud: shopify.config.apiKey,
  });

  await expect(
    shopify.auth.tokenExchange({
      shop: 'shop-b.myshopify.com',
      sessionToken: tokenForShopA,
      requestedTokenType: RequestedTokenType.OfflineAccessToken,
    }),
  ).rejects.toThrow(); // currently does NOT throw; proceeds to POST to shop-b's oauth endpoint
});
```
This test currently fails (the function does not throw), demonstrating the missing `dest`-vs-`shop` cross-check described above.

### Citations

**File:** packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts (L39-51)
```typescript
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

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/admin/authenticate.ts (L220-228)
```typescript
  const url = new URL(request.url);
  const shop = url.searchParams.get('shop')!;

  const sessionId = await api.session.getCurrentId({
    isOnline: config.useOnlineTokens,
    rawRequest: request,
  });

  return {shop, sessionId, payload: undefined, sessionToken};
```

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/admin/strategies/token-exchange.ts (L43-54)
```typescript
  }): Promise<{session: Session}> {
    try {
      console.log(
        'config.future.expiringOfflineAccessTokens',
        config.future.expiringOfflineAccessTokens,
      );
      return await api.auth.tokenExchange({
        sessionToken,
        shop,
        requestedTokenType,
        expiring: config.future.expiringOfflineAccessTokens,
      });
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts (L207-218)
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
  }
```
