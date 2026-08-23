### Title
Token exchange does not bind the untrusted `shop` parameter to the authenticated session-token's `dest` claim - ([File: packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts])

### Summary
`tokenExchange` decodes and cryptographically verifies the session token (proving it was issued by Shopify for *some* shop), but then discards the verified payload and performs the actual OAuth token-exchange call against a completely separate, caller-supplied `shop` string that is never cross-checked against the token's `dest` claim.

### Finding Description
`tokenExchange` in `packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts` does: [1](#0-0) 

It calls `decodeSessionToken(config)(sessionToken)` purely for its side effect (signature/expiry validation) and never inspects the returned payload's `dest`/`sub` fields. The actual shop used for the outbound `/admin/oauth/access_token` request is `sanitizeShop(config)(shop, true)`, where `shop` is a caller-supplied parameter, not derived from the verified token.

Upstream, both the Remix and React Router `TokenExchangeStrategy.authenticate` obtain `shop` from `getShopFromRequest(request)`, which is nothing more than the unauthenticated `?shop=` query string parameter: [2](#0-1) 
and this `shop` value — together with the `sessionToken` header — is passed straight into `exchangeToken` → `api.auth.tokenExchange`: [3](#0-2) [4](#0-3) 

This is the same bug-class as the reported `register_vault` issue: the code authenticates *that a valid credential was presented* (a signature check — Solana signer check / JWT signature check), but never verifies that the credential is *bound to the specific identity/tenant* being acted upon (the target `identity_account` / the target `shop`). Here, a valid session token for shop A can be exchanged for an offline/online access token scoped to shop B, purely because the request's `shop` query parameter says "B" — the library performs no equality check between `payload.dest` and the `shop` argument before calling Shopify's token endpoint.

Whether this is actually exploitable end-to-end depends on Shopify's own `/admin/oauth/access_token` endpoint independently rejecting a subject_token whose audience/dest doesn't match the shop in the URL. That external validation is out of scope of this repo, and the local code provides **no defense-in-depth check of its own** — the resulting `Session` object returned by `createSession` is stamped with `shop: cleanShop` (the untrusted, attacker-controlled shop), regardless of what shop the token was actually issued for: [5](#0-4) 

If the exchange call happens to succeed (e.g., through caching, retries, or any laxness on the Shopify-side check, or in custom/self-hosted mock environments that reuse this library), the resulting `Session` is persisted via `sessionStorage.storeSession` keyed by `getOfflineId(shop)`/`getJwtSessionId(shop, sub)`, using the attacker-supplied `shop`, not the shop cryptographically proven by the JWT. This could poison session storage for a shop the caller does not actually control.

### Impact Explanation
If Shopify's endpoint doesn't strictly enforce dest/shop consistency in all cases, an attacker with a valid session token for a shop they operate could attempt to exchange it while spoofing `shop` to belong to a victim's store, causing the returned session to be persisted under the victim's shop identifier. Even if the request is ultimately rejected server-side by Shopify, the finding demonstrates a missing local invariant: the library trusts the caller for the tenant identifier that a signed credential is supposed to establish on its own — the same "signer authenticated, but the target identity was not bound to the signer" pattern from the original report.

### Likelihood Explanation
This code path is reached by any single merchant/customer-facing embedded app request performing token exchange (`shopify.authenticate.admin` with token-exchange strategy), i.e., unprivileged, standard app flow — not requiring any special access. The missing check is unconditional in the library code, not test-only.

### Recommendation
In `tokenExchange` (`packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts`), capture the payload returned from `decodeSessionToken` and assert that its `dest` (normalized) equals the `sanitizeShop`-normalized `shop` parameter before making the outbound call, throwing `InvalidJwtError`/`InvalidShopError` on mismatch. This mirrors the Solana fix of requiring the identity account to be owned by the authenticated signer, rather than trusting caller-supplied identifiers alongside a validated-but-unbound credential.

### Proof of Concept
Not independently verifiable without live Shopify endpoint behavior; the analysis is based on: (1) `decodeSessionToken` verifies only the JWT signature/exp/aud, (2) its `dest` payload is discarded, (3) `shop` used for the exchange call and for the resulting `Session.shop` comes solely from the caller-controlled query string via `getShopFromRequest`, with no equality check anywhere in `tokenExchange`, `TokenExchangeStrategy.authenticate`, or `exchangeToken`. [1](#0-0) [2](#0-1)

### Citations

**File:** packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts (L32-54)
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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/helpers/get-shop-from-request.ts (L1-4)
```typescript
export function getShopFromRequest(request: Request) {
  const url = new URL(request.url);
  return url.searchParams.get('shop')!;
}
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
