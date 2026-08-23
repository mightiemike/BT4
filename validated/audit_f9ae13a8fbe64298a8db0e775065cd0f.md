Confirmed: in the core `tokenExchange()` function in `packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts:32-78`, the `shop` value used to construct the outbound `https://${cleanShop}/admin/oauth/access_token` request is taken directly from the caller-supplied `shop` parameter, while `decodeSessionToken(config)(sessionToken)` is called only for its signature/expiry/audience-check side effects — its return value (the payload, including the `dest` claim identifying the shop the token was actually issued for) is discarded and never compared against `shop`. All the framework wrappers (`shopify-app-remix`, `shopify-app-react-router`) that call this function derive `shop` from `payload.dest` themselves before calling the strategy, so they are not exploitable this way. But the library's public `shopify.auth.tokenExchange()` API and its documented Node.js/Express usage pattern take `shop` as fully attacker/caller-controlled input independent of the session token's `dest` claim. [1](#0-0) [2](#0-1) 

This is a real architectural gap worth flagging, though I want to note the caveat: I could not confirm from this repo alone whether Shopify's `/admin/oauth/access_token` endpoint itself cross-checks the `dest`/audience of the `subject_token` against the shop domain in the URL path — that enforcement (if it exists) lives server-side at Shopify, not in this client library. Given that uncertainty, and that the rules require proof of a *concrete* forged/cross-tenant outcome reachable purely within this codebase, I'll present this as the identified finding since the analog to the report (an identity/ownership binding that is asserted by the caller rather than cryptographically verified against the signed token) is a structurally exact match, while being transparent about the external dependency.

### Title
Token Exchange Does Not Bind Caller-Supplied `shop` to the Session Token's Signed `dest` Claim - (File: packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts)

### Summary
`tokenExchange()` accepts a `shop` string and a `sessionToken` as independent parameters. It verifies the session token's signature via `decodeSessionToken()`, but never uses the decoded payload to confirm that the token's `dest` (the shop it was actually issued for) matches the `shop` argument used to build the outbound request URL and returned `Session`.

### Finding Description
Analogous to the front-running issue in the external report — where a value asserted by the caller (a public key) is trusted without on-chain proof that the caller controls it — `tokenExchange()` trusts the caller-supplied `shop` string without verifying it against the cryptographically-signed identity embedded in the `sessionToken` (the `dest` claim, which is part of the JWT payload validated by `decodeSessionToken` via `jose.jwtVerify`). Decoding is only used to authenticate the token's issuer/signature/audience, not to bind `shop` to the token: [3](#0-2)  establishes what fields are checked (`aud`, `exp`, `nbf`) — `dest` is never referenced. In `tokenExchange`, the `shop` parameter — not `payload.dest` — is what's sanitized and used both for the request URL and for the resulting `Session.shop`: [4](#0-3) .

By contrast, the framework-level auth strategies correctly derive `shop` from the verified token's `dest` before ever calling into the strategy/token-exchange logic: [5](#0-4) . This shows the library authors know `dest` is the trustworthy source of shop identity, yet the lower-level, publicly documented `shopify.auth.tokenExchange()` API (intended for non-Remix/non-React-Router apps per the docs) does not enforce this invariant itself: [2](#0-1) .

### Impact Explanation
If a caller (e.g., a custom Express/Node integration written against the documented API, where `shop` is taken from `req.query.shop`) passes a `shop` value that doesn't match the token's `dest`, the resulting `Session` object returned by `tokenExchange` is labeled with the caller-chosen `shop`, not the shop cryptographically attested by the token. Any code path that trusts `session.shop` for authorization/session-storage keying downstream could be attributing/exchanging tokens under an incorrect tenant identity, which is the class of "cross-tenant" risk called out in the validation rules. The actual blast radius depends on whether Shopify's server-side `/admin/oauth/access_token` endpoint independently rejects mismatched `dest`/shop pairs — that enforcement is outside this repository and I cannot verify it here.

### Likelihood Explanation
Exploitability is gated by whether Shopify's backend enforces `dest`-to-shop-domain consistency during the OAuth token-exchange grant. If it does (which is the expected secure design for `urn:ietf:params:oauth:grant-type:token-exchange`), the request would simply fail with an error, and the practical impact is limited to defense-in-depth/observability. This library-side gap is real but its exploitability could not be confirmed purely from the shopify-app-js codebase; only apps that build their own custom (non-Remix/non-React-Router) integration around the documented `shop`-from-query-param pattern would be exposed to a mismatch attempt at all.

### Recommendation
Update `tokenExchange()` in `packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts` to use the decoded payload's `dest` (parsed as a hostname) as the authoritative shop value, either by overriding/validating the caller-supplied `shop` against it and throwing (e.g., a new `InvalidJwtError`/`InvalidOAuthError`) on mismatch, mirroring the pattern already used in `authStrategyFactory`'s `getSessionTokenContext`. Update the docs example accordingly so integrators aren't guided toward passing an independent, unchecked `shop` value.

### Proof of Concept
Not independently reproducible end-to-end within this repository, since it depends on Shopify's server-side token-exchange endpoint behavior for a `subject_token` whose `dest` doesn't match the request's shop domain. Within the library itself, the gap can be demonstrated by unit-testing `tokenExchange(config)({shop: 'attacker-shop.myshopify.com', sessionToken: tokenIssuedForVictimShop, ...})` and observing that no error is thrown before the outbound HTTP call is made — confirming `decodeSessionToken`'s payload is discarded and `dest` is never compared to `shop`, as shown in [6](#0-5) .

### Citations

**File:** packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts (L32-76)
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

**File:** packages/apps/shopify-api/lib/session/decode-session-token.ts (L15-40)
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
