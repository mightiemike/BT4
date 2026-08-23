### Title
Token exchange never validates that the `shop` parameter matches the verified `dest` claim in the session token before minting/storing a session - ([File: packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts])

### Summary
This maps to the same bug class as the Solidity report: a value carried inside a signed/verified payload (`acceptedCurrency` in the signing struct) must be cross-checked against the value actually used to execute the transaction, but the code only uses the untrusted/independent value. In `tokenExchange`, the verified session token's `dest` claim (the "trusted currency") is decoded but discarded, while an independently-sourced `shop` string (the "used currency") is used to build the OAuth request URL and to key the persisted `Session`, with no equality check between the two.

### Finding Description
`tokenExchange` in `packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts` decodes the session token purely for validity/signature purposes but never inspects or compares the resulting payload: [1](#0-0) 

The verified JWT payload from `decodeSessionToken` contains a `dest` field that is the cryptographically-attested shop for that token (as used elsewhere, e.g. `dest.hostname` in `getSessionTokenContext`) [2](#0-1) . But in `tokenExchange`, the result of `decodeSessionToken(config)(sessionToken)` is awaited and its return value thrown away; the `shop` used to build `https://${cleanShop}/admin/oauth/access_token` and to create the resulting `Session` (`createSession({..., shop: cleanShop, ...})`) comes exclusively from the `shop` parameter passed in by the caller, which is only run through `sanitizeShop` (a format/regex check, not an authenticity check) [3](#0-2) .

Tracing the caller: in the `shopify-app-remix`/`shopify-app-react-router` admin authentication strategy, for apps using `AppDistribution.ShopifyAdmin` (or non-embedded distribution), `shop` is taken directly from the request's `shop` query/search parameter, not from any verified token claim: [4](#0-3) 

This `shop` value flows unmodified into `TokenExchangeStrategy.exchangeToken` → `api.auth.tokenExchange({sessionToken, shop, ...})`: [5](#0-4) 

Because `tokenExchange` never asserts `shop === payload.dest hostname` (or equivalent), a request whose `shop` query parameter and the shop embedded in a validly-signed session token disagree is accepted anyway: the code trusts the query-supplied `shop` for constructing the OAuth token URL and for the `Session.shop` key used by `getOfflineId`/`getJwtSessionId` and later `sessionStorage.storeSession(...)` calls, even though it holds a verified `sessionToken` proving a different (or no) relationship to that shop.

### Impact Explanation
This is the direct structural analog of the report's core issue: a value asserted by an authenticated/signed artifact (`acceptedCurrency`, here `payload.dest`) is not cross-validated against the value actually used to execute a sensitive action (`shop` used for the OAuth exchange URL and session persistence, analogous to `acceptedCurrency` used in `transferFrom`). If Shopify's OAuth token-exchange endpoint's own server-side enforcement of subject_token/shop binding is weaker than assumed, or if a caller can reach this path with a `shop` value under app-level control (e.g., ShopifyAdmin-distribution flow, where the SDK does not itself verify shop against `dest`), sessions could be created/stored under the wrong shop key, resulting in the app's local session store associating an access token intended for shop A with tenant B (cross-tenant session mix-up), potentially exposing shop A's session/access token to a request that presented shop B's credentials or vice versa.

### Likelihood Explanation
Medium. The `shop` value used in the vulnerable code path for `ShopifyAdmin`-distributed / non-embedded-JWT branches comes straight from the request's own query string rather than from the token payload, meaning the mismatch is trivially reachable by any request that supplies a `shop` parameter different from the session token's `dest`. However, exploitability ultimately also depends on how strictly Shopify's server-side `/admin/oauth/access_token` token-exchange endpoint enforces binding between the `subject_token` and the shop in the URL — a control outside this repository that could not be verified here.

### Recommendation
In `tokenExchange` (`packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts`), capture the payload returned by `decodeSessionToken` and validate that the sanitized `shop` parameter matches the token's `dest` hostname before constructing the request URL or creating the session; reject the exchange (throw `InvalidJwtError`/`InvalidOAuthError`) on mismatch, mirroring how `getSessionTokenContext` already derives `shop` from `dest` for the embedded-JWT branch.

### Proof of Concept
Not independently executable from static review; the concrete exploit condition (whether Shopify's remote OAuth endpoint fully enforces subject_token-to-shop binding) is external to this repository and could not be confirmed with the available tools. The reachable code-level defect — decoding the session token but never comparing its `dest` claim to the `shop` value used for both the outbound OAuth request and session creation — is demonstrated by the cited lines in `token-exchange.ts` and `authenticate.ts`.

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts (L219-228)
```typescript

  const url = new URL(request.url);
  const shop = url.searchParams.get('shop')!;

  const sessionId = await api.session.getCurrentId({
    isOnline: config.useOnlineTokens,
    rawRequest: request,
  });

  return {shop, sessionId, payload: undefined, sessionToken};
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
