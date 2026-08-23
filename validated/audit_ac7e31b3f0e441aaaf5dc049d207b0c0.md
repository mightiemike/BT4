## Title
Unverified `shop` Parameter Not Bound to Session Token Proof in `tokenExchange` Allows Cross-Tenant Session Storage Injection - (File: `packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts`)

### Summary
The `tokenExchange` function decodes and cryptographically verifies the caller-supplied session token (proving it was validly issued by Shopify for *some* shop) but never checks that the token's `dest` claim matches the `shop` parameter that is separately supplied by the request. The `shop` value — not the value bound inside the verified token — is what gets used both for the outbound `access_token` request and, critically, for constructing/persisting the resulting `Session` object.

### Finding Description
`tokenExchange()` calls `decodeSessionToken(config)(sessionToken)` purely to validate the token's signature/exp/aud, but discards its return value entirely: [1](#0-0) 

The verified payload's `dest` (the shop the token was actually minted for) is never compared against the `shop` argument. The `cleanShop` used to build the request URL and the persisted `Session.shop` come exclusively from the untrusted `shop` parameter: [2](#0-1) 

This is structurally identical to the `OidcRecoveryValidator.startRecovery` bug: a value that logically must be bound to the cryptographic proof (`pendingPasskeyHash` ↔ ZK proof; `shop` ↔ session token `dest`) is instead accepted from an independent, attacker-influenced input and persisted without cross-checking against the proof.

Notably, the library's own documentation encourages exactly this unsafe pattern — extracting `shop` from the raw request query rather than from the verified token: [3](#0-2) 

By contrast, the safer internal call sites in `shopify-app-express` and `shopify-app-remix`/`shopify-app-react-router` derive `shop` directly from the verified `payload.dest` before calling `tokenExchange`, showing that binding is possible and expected, but is not enforced by the shared library function itself: [4](#0-3) [5](#0-4) 

Any app route built following the documented pattern (`shop` from `req.query.shop`, session token from header/URL param) — or any other custom `/auth` route that accepts a `shop` query param independently of the token — inherits this gap: a user holding a validly-signed session token for Shop A can supply an arbitrary `shop` value for Shop B in the same request, and the library will silently persist a `Session` object stamped with `shop: ShopB` (and, depending on the outcome of the remote call, potentially an access token tied to a different session identity than the one the token proves).

### Impact Explanation
If exploited, this results in session-storage records being created/keyed under a shop that was never cryptographically attested by the presented proof — a session-storage injection / cross-tenant confusion. Even absent a successful remote grant, the local `Session` object (`shop: cleanShop`) is built from the unverified parameter, meaning any downstream logic in the calling app that trusts `tokenExchange`'s returned `session.shop` as equivalent to "the shop that authenticated" is misled to whichever shop value the caller happened to attach to the request.

### Likelihood Explanation
Any single authenticated merchant/customer who can call an app's token-exchange endpoint controls both parameters of the request (the `shop` query string and, if the app is embedded, their own valid session token). No secret leakage, MITM, or privileged access is required — only using the library exactly as documented.

### Recommendation
Inside `tokenExchange()`, capture the decoded payload and assert `sanitizeShop(config)(new URL(payload.dest).hostname, true) === cleanShop` (or simply always derive `cleanShop` from `payload.dest` instead of the caller-supplied `shop`) before making the remote request or constructing the `Session`. Update the documentation example to derive `shop` from the verified token rather than `req.query.shop`.

### Proof of Concept
1. Obtain a valid, unexpired session token for `shop-a.myshopify.com` (e.g. as a normal authenticated user of that shop's embedded app instance).
2. Following the documented pattern in `tokenExchange.md`, call the app's `/auth` route with `?shop=shop-b.myshopify.com` while presenting the Shop A session token via the `Authorization: Bearer` header.
3. `tokenExchange()` verifies only the token's signature/exp/aud — not its `dest` — then issues the exchange call against `https://shop-b.myshopify.com/admin/oauth/access_token` and, on any success response, stores a `Session` with `shop: 'shop-b.myshopify.com'`, derived purely from the attacker-controlled query parameter rather than the proof. [6](#0-5)

### Citations

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

**File:** packages/apps/shopify-api/docs/reference/auth/tokenExchange.md (L14-26)
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
```

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L65-72)
```typescript
  try {
    const payload = await api.session.decodeSessionToken(sessionToken);
    const shop = new URL(payload.dest).hostname;
    const sub = payload.sub;

    const sessionId = config.useOnlineTokens
      ? api.session.getJwtSessionId(shop, sub)
      : api.session.getOfflineId(shop);
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
