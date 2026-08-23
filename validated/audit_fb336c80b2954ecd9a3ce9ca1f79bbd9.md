### Title
Missing verification that the `shop` parameter matches the session token's `dest` claim before performing OAuth token exchange - (File: packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts)

### Summary
The report describes `OffchainDNSResolver.resolveCallback` blindly forwarding to a nested untrusted callee without validating that the response actually originates from/pertains to the expected entity (the EIP-3668 `sender` check). The analogous pattern in `shopify-app-js` is `tokenExchange()` in [1](#0-0) , which decodes/verifies the caller-supplied session token but never cross-checks the verified token's `dest` (the shop the token was actually minted for) against the separately caller-supplied `shop` parameter used to build the OAuth token-exchange request URL.

### Finding Description
`tokenExchange()` does this: [2](#0-1) 

It calls `decodeSessionToken(config)(sessionToken)` — which validates the JWT signature and `aud` — but discards the returned payload entirely (`await decodeSessionToken(config)(sessionToken);`), never comparing `payload.dest` to the `shop` argument. It then builds the access-token request purely from the caller-supplied `shop`:
```
const cleanShop = sanitizeShop(config)(shop, true)!;
... POST https://${cleanShop}/admin/oauth/access_token ...
```
This mirrors the CCIP bug class: a value returned/verified from one trusted source (the JWT `dest`) is not reconciled against a second, independently supplied value (`shop`) before it is used to construct an outbound authenticated request — exactly the "MUST catch/validate the sender field" pattern the EIP requires and the finding says is missing.

In the higher-level strategies that call `api.auth.tokenExchange`, the `shop` value fed into this function is derived from `sessionContext.shop`, which is computed by `getShopFromRequest(request)` (a raw, unauthenticated `?shop=` URL query parameter) in some code paths, e.g.: [3](#0-2) 

versus the properly verified `dest.hostname` derived from the JWT in the embedded-app branch: [4](#0-3) 

I was not able to fully trace, within the available iterations, every calling context in `shopify-app-remix`/`shopify-app-react-router` `token-exchange.ts` strategies to confirm a code path where an attacker-controlled `shop` query parameter (rather than the JWT-derived `dest`) is passed into `exchangeToken`/`tokenExchange` for an *embedded* (ShopifyAdmin distribution) request — the branches I reviewed (`getSessionTokenContext`) do use `dest.hostname` for the embedded-app / ShopifyAdmin-distribution path, and the raw query param is used only for the non-JWT/custom-app cookie-session path. Without confirming a reachable path where a forged `shop` reaches `tokenExchange` alongside a session token issued for a *different* shop, I cannot assert full end-to-end exploitability from an anonymous request.

### Impact Explanation
If reachable with a mismatched `shop`/token pair, this could allow an offline/online access token to be requested against a shop the session token was not issued for (cross-tenant token confusion), analogous to the sender-mismatch impact in the original finding. However, Shopify's own `/admin/oauth/access_token` token-exchange endpoint on the platform side is expected to validate that the subject_token (`sessionToken`) actually corresponds to the target shop domain in the URL, which would likely reject a mismatched request server-side. This external validation significantly limits real-world impact, and I could not confirm from the code alone that Shopify's endpoint does not perform this check (that behavior lives outside this repo).

### Likelihood Explanation
Low-to-moderate: the missing local defense-in-depth check exists in code (`payload` from `decodeSessionToken` is discarded without comparison to `shop`), but I could not confirm within this codebase a concrete reachable path where an anonymous/attacker-controlled `shop` value (independent of the verified token) is passed into `tokenExchange` for the primary embedded-admin flow. The likelihood is contingent on this unconfirmed reachability and on the (unverifiable from this repo) server-side validation behavior of Shopify's token-exchange endpoint.

### Recommendation
In `tokenExchange()` (`packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts`), use the decoded/verified payload's `dest` field as the authoritative shop, or explicitly assert `sanitizeShop(config)(shop) === new URL(payload.dest).hostname` before proceeding, throwing an `InvalidJwtError`/`InvalidOAuthError` on mismatch, mirroring the EIP-3668 recommendation to validate the "sender" of a nested/verified response before trusting it in a follow-up privileged action.

### Proof of Concept
Not independently reproducible with certainty from the indexed code alone — the analog requires confirming a caller path that supplies a `shop` value not derived from the verified session token's `dest` claim to `api.auth.tokenExchange`. This should be validated with a live Devin session that can trace all callers of `tokenExchange`/`exchangeToken` across `shopify-app-remix`, `shopify-app-react-router`, and `shopify-app-express` to determine if any embedded/ShopifyAdmin-distribution request path permits an unauthenticated caller to supply a `shop` query parameter independent of the JWT `dest` before this function is invoked.

### Citations

**File:** packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts (L32-55)
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
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/helpers/get-shop-from-request.ts (L1-6)
```typescript
export function getShopFromRequest(request: Request) {
  const url = new URL(request.url);
  return url.searchParams.get('shop')!;
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
