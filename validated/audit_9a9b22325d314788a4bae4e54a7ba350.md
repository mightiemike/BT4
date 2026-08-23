## Title
Missing binding check between `shop` and the session-token's own `dest`/`iss` claim in `tokenExchange()` - (File: `packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts`)

### Summary
`tokenExchange()` decodes and cryptographically verifies the caller-supplied session token but discards the decoded payload, then uses a separately-supplied `shop` string (not derived from, or checked against, the token's own `dest`/`iss` claim) to build the `/admin/oauth/access_token` request URL and to construct the resulting `Session`. This mirrors the CollateralToken pattern of validating that a piece of signed/attacker-influenced data is *authentic* while never checking that it *corresponds to* the other identifier (shop) the rest of the operation is keyed on.

### Finding Description
In `tokenExchange()`: [1](#0-0) 
the function calls `await decodeSessionToken(config)(sessionToken);` purely for its side effect of throwing on an invalid signature/expiry/audience — the returned `JwtPayload` (which contains `dest`/`iss`, i.e. the shop the token was actually issued for) is never captured or compared against the `shop` argument. The `shop` argument is instead sanitized and used directly to build the outbound request: [2](#0-1) 
and to label the resulting `Session`: [3](#0-2) 

Because the JWT is signed with the app's single shared `apiSecretKey` (not a per-shop secret), `decodeSessionToken` will successfully validate *any* session token issued to *any* shop that has installed the app, as long as it's signed by the correct key and has the correct `aud`: [4](#0-3) 
Nothing in this library code enforces that the `dest` inside that token equals the `shop` value being used to construct the request/session.

This lack-of-binding is reachable from at least one call path where `shop` is not derived from a freshly-validated token: in the `shopify-app-remix`/`shopify-app-react-router` `getSessionTokenContext`, when `config.isEmbeddedApp` is `false` (or, more importantly, in the `AppDistribution.ShopifyAdmin` branch), `shop` is taken directly from the request's `?shop=` query parameter instead of from a verified token payload, while an unrelated `sessionToken` (whichever bearer/`id_token` happens to be attached to the request) is still forwarded down to `TokenExchangeStrategy.authenticate()` → `api.auth.tokenExchange()`: [5](#0-4) [6](#0-5) 

### Impact Explanation
If exploitable end-to-end, this pattern would let a token that is authentic-but-for-a-different-shop be exchanged/labelled as belonging to an attacker-chosen `shop` string inside this library's local bookkeeping (the constructed `Session` object, and which shop's session-storage key gets written to). That is the same class of defect as the original report: authenticity of the "extra data" (session token) is checked, but not its correspondence to the identifier (shop/collateralId) the rest of the transaction is keyed on.

However, I could not confirm a concrete, self-contained impact purely within this repository: the actual token issuance happens on Shopify's own `/admin/oauth/access_token` endpoint, which independently knows the real shop tied to the `subject_token` it receives and is expected to reject a token/shop mismatch (this is enforced server-side, outside this codebase). Whether `TokenExchangeStrategy` is ever reachable with a `shop` value that wasn't already cross-checked elsewhere (e.g., via `validateShopAndHostParams`, HMAC-validated OAuth callback, or the `AppDistribution.ShopifyAdmin` branch's separate session-cookie/currentId lookup) is also not fully verifiable from the indexed code alone — I was not able to trace `shopify-app.ts`'s strategy-selection logic within the remaining budget to determine exactly which distribution/embedding combinations route through `TokenExchangeStrategy` with an unverified `shop`.

### Likelihood Explanation
Requires an attacker to already control a valid session token for *some* shop (i.e. be a legitimate/malicious merchant of an app-installed shop) and to be able to influence the `shop` query parameter independent of the token's own claim on a request path that reaches `tokenExchange()` without the `dest`/`shop` cross-check that other call sites (e.g., `packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts`, which always derives `shop` from `payload.dest`) already perform correctly. This is a narrower/less certain condition than the original CollateralToken exploit, and the remote authorization server is likely the actual safety net.

### Recommendation
In `tokenExchange()`, capture the decoded payload and assert that `new URL(payload.dest).hostname` (or `payload.iss`) matches the sanitized `shop` argument before using `shop` to build the request/session, e.g.:
```ts
const payload = await decodeSessionToken(config)(sessionToken);
const cleanShop = sanitizeShop(config)(shop, true)!;
if (new URL(payload.dest).hostname !== cleanShop) {
  throw new ShopifyErrors.InvalidJwtError('Session token shop does not match requested shop');
}
```
This makes local defense-in-depth explicit rather than relying solely on Shopify's remote OAuth endpoint to reject mismatches, consistent with how `perform-token-exchange.ts` already derives `shop` strictly from `payload.dest`.

### Proof of Concept
Not fully demonstrable from the indexed codebase alone: exploitation depends on (a) confirming a live code path (distribution/embedding combination) where `TokenExchangeStrategy.authenticate()` is invoked with a `shop` sourced from an unauthenticated request parameter while a differently-scoped valid `sessionToken` is attached, and (b) confirming that Shopify's real `/admin/oauth/access_token` endpoint does not itself reject the resulting mismatched `subject_token`/target-shop combination. Both points require verification beyond what the available index/tools could establish with confidence — I recommend a Devin session with full repository and, ideally, a live Shopify sandbox to construct and confirm the exact reachable path and remote-endpoint behavior before treating this as confirmed-exploitable.

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts (L220-228)
```typescript
  const url = new URL(request.url);
  const shop = url.searchParams.get('shop')!;

  const sessionId = await api.session.getCurrentId({
    isOnline: config.useOnlineTokens,
    rawRequest: request,
  });

  return {shop, sessionId, payload: undefined, sessionToken};
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts (L45-65)
```typescript
  public async authenticate(
    request: Request,
    sessionContext: SessionContext,
  ): Promise<Session> {
    const {api, config, logger} = this;
    const {shop, session, sessionToken} = sessionContext;

    if (!sessionToken) throw new InvalidJwtError();

    if (
      !session ||
      !session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)
    ) {
      logger.info('No valid session found', {shop});
      logger.info('Requesting offline access token', {shop});
      const {session: offlineSession} = await this.exchangeToken({
        request,
        sessionToken,
        shop,
        requestedTokenType: RequestedTokenType.OfflineAccessToken,
      });
```
