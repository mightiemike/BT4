### Title
`shopify.auth.tokenExchange` validates the session token's signature but performs the OAuth token exchange against a caller-supplied `shop` value that is never checked against the token's `dest` claim - ([File: packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts])

### Summary
This is a structural analog of the reported bug class: a value is checked/validated (the `min_output_amount` slippage check against `assets`, in the original report), but the value actually used for the sensitive action differs from the value that was validated (the real payout is `liquidation_amount`). In `tokenExchange`, `decodeSessionToken` cryptographically verifies the JWT session token (signature + `aud`/api-key check), but the result of that verification (the payload, including the shop-binding `dest` claim) is discarded. The actual OAuth token-exchange request is built using the separately supplied `shop` parameter, with no assertion that it matches the token's `dest`.

### Finding Description
`tokenExchange` in `packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts` does:

```ts
export function tokenExchange(config: ConfigInterface): TokenExchange {
  return async ({shop, sessionToken, requestedTokenType, expiring}: TokenExchangeParams) => {
    await decodeSessionToken(config)(sessionToken);
    ...
    const cleanShop = sanitizeShop(config)(shop, true)!;
    const postResponse = await fetchRequestFactory(config)(
      `https://${cleanShop}/admin/oauth/access_token`,
      { method: 'POST', body: JSON.stringify(body), ... },
    );
    ...
    return {session: createSession({..., shop: cleanShop, ...})};
  };
}
``` [1](#0-0) 

`decodeSessionToken` only checks JWT signature validity and (optionally) the `aud` claim against `config.apiKey`; it does not verify that the caller-provided `shop` corresponds to `payload.dest`: [2](#0-1) 

The return value of `decodeSessionToken` is discarded (`await decodeSessionToken(config)(sessionToken);` with no captured payload), so the code has no way to cross-check `shop` against `dest`. The created `Session` is then persisted keyed by `cleanShop` — the attacker-controlled value — not by the value bound in the verified token.

This mirrors the report's root cause exactly: a validation is performed on one value (the JWT/`assets` calculation) while the operation that has real consequences (`fetch to https://{shop}/admin/oauth/access_token` and `session.shop = cleanShop` / `liquidation_amount` transfer) is driven by a different, unchecked value.

The library's own consumers (`shopify-app-express`, `shopify-app-remix`, `shopify-app-react-router`) call `tokenExchange`/the equivalent strategy using the `shop` value extracted from `payload.dest` after decoding the token themselves (e.g. `getSessionTokenContext`) [3](#0-2) , so in the shipped app frameworks the mismatch is not directly reachable through the front door. However, `shopify.auth.tokenExchange` is a public, documented API of `@shopify/shopify-api`, and its own reference documentation explicitly shows constructing `shop` from `req.query.shop` (a value fully independent of, and unrelated to, the session token) rather than from the decoded token payload: [4](#0-3) 

Any app built directly against `shopify-api` (bypassing the higher-level `shopify-app-*` wrappers) that follows this documented pattern will pass an unauthenticated `shop` query parameter alongside a session token without the library enforcing that they match.

### Impact Explanation
If an app (following the library's own documented usage pattern) calls `tokenExchange({shop: req.query.shop, sessionToken, ...})`, an attacker who possesses *any* valid session token (e.g., one legitimately issued for their own installed shop) could supply an arbitrary `shop` query parameter for a different, victim shop. The library will not reject this combination — it only validates the JWT's signature/audience, not shop binding — and will proceed to call `https://{attacker-supplied-shop}/admin/oauth/access_token` and store the resulting session under that shop's ID. Whether this ultimately succeeds is bounded by whatever validation Shopify's OAuth server performs server-side on the `subject_token` versus the target shop, which is outside this library's control and could not be verified from the codebase alone. Regardless of that external mitigating factor, the library itself omits an available, cheap defense-in-depth check (`payload.dest === shop`) that the accompanying report class calls out as the root cause: checking one thing while acting on another.

### Likelihood Explanation
Likelihood depends on whether app developers use `shopify.auth.tokenExchange` directly with a request-supplied `shop` (as the official docs recommend) versus deriving `shop` strictly from the decoded token (as the higher-level `shopify-app-*` packages internally do). Given the documented usage explicitly sources `shop` from `sanitizeShop(req.query.shop, true)`, independent of the session token, this is a realistic, encouraged pattern for `shopify-api` consumers, making the missing binding check a genuine, low-effort-to-trigger latent condition, gated on Shopify's server-side enforcement.

### Recommendation
Capture the payload returned by `decodeSessionToken` in `tokenExchange` and assert that `new URL(payload.dest).hostname === cleanShop` before issuing the token-exchange request, throwing `InvalidJwtError`/`InvalidShopError` on mismatch — analogous to moving/adding the correct check the report recommends (checking the value that is actually used for the sensitive operation, not just a related one). Additionally, update the `tokenExchange` reference documentation to derive `shop` from the decoded token's `dest` rather than an untrusted query parameter.

### Proof of Concept
Conceptual (not executed, since this requires interaction with Shopify's real OAuth backend to fully confirm end-to-end impact):
1. Attacker installs the app on `attacker-shop.myshopify.com` and obtains a legitimately signed session token for that shop.
2. Attacker calls the app's token-exchange endpoint (built per the library's documented example) with `shop=victim-shop.myshopify.com` and the session token issued for `attacker-shop`.
3. `tokenExchange` calls `decodeSessionToken` — which only checks signature/`aud` — successfully, then proceeds to call `https://victim-shop.myshopify.com/admin/oauth/access_token` with the attacker's `subject_token`, and (if Shopify's backend does not itself reject the shop/token mismatch) stores a `Session` keyed to `victim-shop.myshopify.com`. [5](#0-4)

### Citations

**File:** packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts (L32-78)
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
