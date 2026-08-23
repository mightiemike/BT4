### Title
Cross-tenant impersonation in `MerchantCustomAuth.authenticate` via unauthenticated `shop` query parameter - (File: packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/merchant-custom-app.ts)

### Summary
`MerchantCustomAuth.authenticate` mints a session with the app's static configured access token purely from `sessionContext.shop`, performing zero JWT/HMAC verification. For this strategy, `config.isEmbeddedApp` is `false` (as shown in the test fixture using `AppDistribution.ShopifyAdmin`), so the upstream `getSessionTokenContext` in `authenticate.ts` takes the branch that reads `shop` directly and unauthenticated from the request's URL query string instead of validating a signed session token.

### Finding Description
`MerchantCustomAuth.authenticate` at [1](#0-0)  takes `sessionContext.shop` and calls `this.api.session.customAppSession(shop)` with no cryptographic check at all. The `customAppSession` implementation in the underlying API package simply sanitizes the shop domain format and constructs a `Session` object — it performs no ownership or tenant check: [2](#0-1) .

`sessionContext.shop` is produced upstream in `getSessionTokenContext` inside `authStrategyFactory`: [3](#0-2) . That function branches on `config.isEmbeddedApp`:
- If `config.isEmbeddedApp` is true, it calls `validateSessionToken` to cryptographically verify a JWT and derives `shop` from the verified `dest` claim.
- If `config.isEmbeddedApp` is false, it falls straight through to `const shop = url.searchParams.get('shop')!;` — an attacker-controlled, completely unverified value.

The test fixture for `MerchantCustomAuth` confirms real-world usage sets `isEmbeddedApp: false` together with `distribution: AppDistribution.ShopifyAdmin`: [4](#0-3) . With this default (non-embedded) configuration, `validateShopAndHostParams` also becomes a no-op because it is gated by `if (config.isEmbeddedApp)`: [5](#0-4) .

Consequently, for a `MerchantCustomAuth`-configured app, no upstream code verifies that `sessionContext.shop` corresponds to the shop the request is legitimately for — it is taken verbatim (only regex-sanitized for domain format) from the `?shop=` query parameter, with no signature, cookie, or JWT binding it to the requester.

### Impact Explanation
This maps to Shopify's cross-tenant/session-forgery impact class, but the practical severity is constrained by the deployment model: `AppDistribution.ShopifyAdmin` custom apps are single-tenant — they hold exactly one static Admin API access token issued for one specific shop, obtained by that shop's own merchant through the "generate app access token" flow in that shop's Admin. There is no multi-tenant secret or session storage lookup keyed by `shop` in this strategy (unlike `AuthCodeFlowStrategy`/`TokenExchangeStrategy`, which use `config.sessionStorage`). Supplying an arbitrary `shop=<attacker-tenant>.myshopify.com` does not grant access to another *installed* tenant's data; it only changes the `Session.shop` field label on a `Session` object that still carries the single static `adminApiAccessToken` configured for the one shop this custom app was built for. Any Admin API calls made with that access token against Shopify's servers are still authorized by Shopify only for the shop that issued the token, since Shopify itself validates the access token against its own shop record — an attacker cannot make the static token act on a different, unauthorized shop merely by relabeling `session.shop` locally. Thus, this does not produce a demonstrated cross-tenant data breach against Shopify's Admin API; the impact is limited to internal misrouting of app-level logic that keys off `session.shop` (e.g., app database or per-shop feature flags mistakenly reading/writing config for the attacker-chosen shop string), which is a use-after-misconfiguration bug in the calling app rather than a full session/token forgery against Shopify.

### Likelihood Explanation
Trivial to trigger: any anonymous request lacking the (in this mode, non-required) session-token header and setting `?shop=` reaches this code path with default config for `ShopifyAdmin`-distributed apps. However, this is inherent to the single-tenant nature of `AppDistribution.ShopifyAdmin` custom apps as documented/intended by Shopify (no OAuth/JWT flow is expected for this distribution type), rather than a exploitable defect that breaches tenant isolation against Shopify's backend, since the access token itself is Shopify-side bound to one shop.

### Recommendation
If a stronger guarantee is desired, `MerchantCustomAuth.authenticate` (or `validateShopAndHostParams`) should validate that `sessionContext.shop` matches an explicitly configured expected shop (e.g., a `config.shop` value set at app configuration time) before minting the `customAppSession`, rejecting the request otherwise, rather than trusting the raw `?shop=` query parameter for the `Session.shop` field.

### Proof of Concept
```ts
// packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/__tests__/merchant-custom/authenticate.test.ts
it('accepts an unverified shop from the query string with no session token', async () => {
  const config = testConfig({
    isEmbeddedApp: false,
    distribution: AppDistribution.ShopifyAdmin,
    adminApiAccessToken: 'test-token',
  });
  const shopify = shopifyApp(config);

  const attackerShop = 'attacker-tenant.myshopify.com';
  const {session} = await shopify.authenticate.admin(
    new Request(`${APP_URL}?shop=${attackerShop}`), // no Authorization header
  );

  // Currently session.shop === attackerShop despite zero verification.
  expect(session.shop).toBe(attackerShop);
});
```
This demonstrates `session.shop` is fully attacker-controlled with no signature check, though (per the impact analysis above) it does not by itself yield access to another shop's real Admin API data because the static access token remains bound server-side to the originally configured shop.

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/merchant-custom-app.ts (L35-48)
```typescript
  public async authenticate(
    _request: Request,
    sessionContext: SessionContext,
  ): Promise<Session | never> {
    const {shop} = sessionContext;

    this.logger.debug(
      'Building session from configured access token for merchant custom app',
      {shop},
    );
    const session = this.api.session.customAppSession(shop);

    return session;
  }
```

**File:** packages/apps/shopify-api/lib/session/session-utils.ts (L86-94)
```typescript
export function customAppSession(config: ConfigInterface) {
  return (shop: string): Session => {
    return new Session({
      id: '',
      shop: `${sanitizeShop(config)(shop, true)}`,
      state: '',
      isOnline: false,
    });
  };
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts (L189-228)
```typescript
async function getSessionTokenContext(
  params: BasicParams,
  request: Request,
): Promise<SessionTokenContext> {
  const {api, config, logger} = params;

  const headerSessionToken = getSessionTokenHeader(request);
  const searchParamSessionToken = getSessionTokenFromUrlParam(request);
  const sessionToken = (headerSessionToken || searchParamSessionToken)!;

  logger.debug('Attempting to authenticate session token', {
    shop: getShopFromRequest(request),
    sessionToken: JSON.stringify({
      header: headerSessionToken,
      search: searchParamSessionToken,
    }),
  });

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts (L5-20)
```typescript
export function validateShopAndHostParams(
  params: BasicParams,
  request: Request,
) {
  const {api, config, logger} = params;

  if (config.isEmbeddedApp) {
    const url = new URL(request.url);
    const shop = api.utils.sanitizeShop(url.searchParams.get('shop')!);
    if (!shop) {
      logger.debug('Missing or invalid shop, redirecting to login path', {
        shop,
      });
      throw redirectToLoginPath(request, params);
    }

```
