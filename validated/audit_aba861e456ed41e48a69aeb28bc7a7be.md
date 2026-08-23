### Title
`tokenExchange` binds the returned access token to a caller-supplied `shop` without verifying it against the session token's own `dest` claim - ([File: packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts])

### Summary
The external report's core lesson is: never trust an assumed value passed alongside an operation — always verify the actual value produced by the trusted source (contract balance) before recording it. `tokenExchange` in `shopify-app-js` exhibits the same anti-pattern in an OAuth/session context: it decodes and cryptographically validates the `sessionToken`, but then discards the decoded payload and uses the separately-supplied `shop` parameter — not the token's own `dest` claim — as the shop of record for both the outbound request and the resulting `Session`.

### Finding Description
`tokenExchange` decodes the session token purely to validate its signature/expiry, but never checks that `payload.dest` matches the `shop` argument: [1](#0-0) 

The decoded payload's return value is discarded entirely (`await decodeSessionToken(config)(sessionToken);`), and `cleanShop` (derived only from the caller-provided `shop`) is what gets used to build the outbound request URL and to stamp the resulting `Session.shop`: [2](#0-1) 

This mirrors the ERC20 finding's root cause: the function trusts an externally-supplied "expected" value (`shop`) instead of deriving the authoritative value from the verified artifact (`payload.dest` inside the signed token), and never reconciles the two. In the safe callers that ship in this monorepo (`shopify-app-express`'s `performTokenExchange`, and the remix/react-router `getSessionTokenContext`), `shop` happens to be derived from `payload.dest` before being passed in: [3](#0-2) [4](#0-3) 

However, `shopify.auth.tokenExchange` is a public, documented API (as shown by its own dedicated test suite) that any app author can call directly with any `shop`/`sessionToken` combination: [5](#0-4) 

Because the library itself performs no internal cross-check between `shop` and the token's `dest`, any integration path where `shop` is taken from an untrusted source (e.g., a query/body parameter on a custom route, rather than derived from `payload.dest`) allows the token-exchange request to be issued for a shop that does not match the shop that actually issued the session token.

### Impact Explanation
If a consuming application (or a future/alternate integration inside this monorepo) passes an app-controlled or request-controlled `shop` value instead of deriving it from the verified token payload, a merchant/customer session token from shop A could be used to request/persist a session keyed to shop B (`cleanShop`), causing a cross-tenant session-storage write under the wrong shop key. Because `Session.shop` and the session-storage id are derived from the untrusted `shop`, this can lead to session data (including the returned access token) being associated with, and potentially retrievable/queryable under, the wrong shop — a cross-tenant session-storage injection risk in the specific case that a caller's `shop` argument isn't already tied to `dest`.

### Likelihood Explanation
Low-to-Medium. The two production call sites bundled in this repo (`shopify-app-express`, `shopify-app-remix`/`shopify-app-react-router`) already do the correct thing by deriving `shop` from `payload.dest`, so the risk in the shipped framework paths themselves is not directly exploitable as-is (this could not be fully confirmed as *always* the case across every code path, since the index does not surface every call site). The residual risk is that `tokenExchange` is exported as a standalone public API without an internal safety check, so any external app author (or a currently un-reviewed internal call site) supplying `shop` from a different, less-trusted source than `payload.dest` reintroduces the mismatch with no defense from the library.

### Recommendation
Mirror the "verify actual vs. assumed" mitigation from the report: after decoding the token, compare the verified value against the caller-supplied one and reject on mismatch, rather than silently trusting the caller's input.

```ts
const payload = await decodeSessionToken(config)(sessionToken);
const cleanShop = sanitizeShop(config)(shop, true)!;
const tokenShop = sanitizeShop(config)(new URL(payload.dest).hostname, true);

if (cleanShop !== tokenShop) {
  throw new ShopifyErrors.InvalidJwtError('Session token shop does not match requested shop');
}
```

This ensures the shop used to build the outbound request and to stamp the resulting `Session` is always the one cryptographically attested by the token, not a value trusted purely because it was passed as an argument.

### Proof of Concept
1. Obtain a validly signed session token for `shop-a.myshopify.io` (e.g., via legitimate embedded-app flow, `dest: "https://shop-a.myshopify.io"`).
2. Call `shopify.auth.tokenExchange({ shop: "shop-b.myshopify.io", sessionToken, requestedTokenType })` directly (as the public API allows, per `packages/apps/shopify-api/lib/auth/oauth/__tests__/token-exchange.test.ts`).
3. Observe that the code builds the request to `https://shop-b.myshopify.io/admin/oauth/access_token` and, if the Shopify OAuth server accepted the exchange, would construct/persist a `Session` with `shop: "shop-b.myshopify.io"` — i.e., a session record for shop B built from a token that was only ever proven to belong to shop A, with no local check comparing `payload.dest` to the `shop` argument. [6](#0-5)

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

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/admin/authenticate.ts (L207-218)
```typescript
  if (config.distribution !== AppDistribution.ShopifyAdmin) {
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

**File:** packages/apps/shopify-api/lib/auth/oauth/__tests__/token-exchange.test.ts (L61-65)
```typescript
      const tokenExchangeResponse = await shopify.auth.tokenExchange({
        shop,
        sessionToken,
        requestedTokenType: RequestedTokenType.OnlineAccessToken,
      });
```
