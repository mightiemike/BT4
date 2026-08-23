### Title
Merchant Custom App (`AppDistribution.ShopifyAdmin`) authentication trusts unauthenticated `shop` query parameter to route the app's real Admin API access token - ([File: packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts])

### Summary
For apps configured with `distribution: AppDistribution.ShopifyAdmin` ("merchant custom apps"), `shopify.authenticate.admin()` derives the `shop` used to build the Admin/Storefront API session **directly from the unauthenticated `shop` URL query parameter**, with no OAuth session, no signed cookie, and no verified session token to bind that value to the caller's identity. That `shop` is then handed to `api.session.customAppSession(shop)`, which builds a `Session` carrying the app's single, statically configured `adminApiAccessToken` for whatever `shop` value the requester supplied. This mirrors the root cause of the reference finding: an identity/target parameter taken from attacker-controlled request data is trusted as if it had been authenticated, instead of being tied to a verified `msg.sender`-equivalent (a validated session/token).

### Finding Description
In `getSessionTokenContext` (shared shape in both `shopify-app-remix` and `shopify-app-react-router`), the `ShopifyAdmin` distribution branch reads `shop` with zero verification: [1](#0-0) 

Compare this with the branch used for every other distribution, which requires a cryptographically validated session token (`validateSessionToken`) before trusting `shop`: [2](#0-1) 

The `ShopifyAdmin` distribution exists specifically for "merchant custom apps," which are documented as **not** using OAuth at all and instead relying on a single, statically configured `adminApiAccessToken` for the one shop that installed the app: [3](#0-2) 

The resulting `shop` (still just the raw/URL-decoded query param at this point) is passed on to `api.session.customAppSession(shop)` to build the request session, as shown by the merchant-custom strategy test: [4](#0-3) 

`customAppSession` does run the value through `sanitizeShop`, which restricts the value to the `*.myshopify.com` / `*.myshopify.io` / `*.shopify.com` domain family (or any configured `domainTransformations`) — it is not open to arbitrary hosts: [5](#0-4) 

However, `sanitizeShop` only validates the **format** of the domain — it does not verify that the caller is actually authorized to act as that shop. Any anonymous requester can therefore choose **any** syntactically valid `*.myshopify.com`/`*.myshopify.io` domain (including a store they themselves control) and the library will build a `Session` object binding that attacker-chosen shop to the app's real, permanent `adminApiAccessToken`. Downstream Admin/Storefront GraphQL/REST clients construct their request URL from `session.shop`, meaning the app's genuine, secret access token gets sent in a request to a domain fully chosen by the anonymous caller.

### Impact Explanation
This is a credential-disclosure / SSRF-style issue: an anonymous HTTP requester can force the app server to authenticate to a shop of the attacker's choosing (e.g., a store the attacker owns) using the app's real Admin API access token. The attacker's store can log the token, and depending on the scopes granted, that token can then be replayed to access the *actual* merchant's data (since Admin access tokens for `ShopifyAdmin`-distribution apps are typically long-lived and not scoped to a single request). At minimum it allows an attacker to trigger outbound requests carrying a secret credential to a destination they control, and lets an unauthenticated actor manipulate which shop the app's privileged Admin session methods will target. This directly parallels the reference bug: an unauthenticated request supplies an "identity" (`shop`, analogous to `sender`) that the module trusts for a privileged operation without checking it against any authenticated context.

### Likelihood Explanation
High for any deployment of `AppDistribution.ShopifyAdmin` that relies solely on `shopify.authenticate.admin()` for protection (as its own documentation implies is expected, since it says "up to the developer... to add login and authentication functionality" but the library's own `authenticate.admin` helper does not enforce that). Any anonymous GET/POST to a route calling `shopify.authenticate.admin(request)` with a crafted `?shop=` value is sufficient — no secret, cookie, or prior interaction is required.

### Recommendation
For the `AppDistribution.ShopifyAdmin` strategy, do not derive `shop` from the raw request query string. Either:
- Bind the `shop` to a value fixed at app configuration/build time (single-tenant custom apps are, by design, for exactly one shop), or
- Require the caller to be authenticated through the developer's own auth layer before `shop` is trusted, and validate that the resulting `shop` matches the shop the `adminApiAccessToken` was actually issued for (e.g. store the expected shop in app config and assert equality rather than accepting an arbitrary URL parameter).

### Proof of Concept
1. Deploy an app with `distribution: AppDistribution.ShopifyAdmin` and a real `adminApiAccessToken` (as shown in the CHANGELOG example).
2. As an anonymous client, send `GET /app?shop=attacker-owned-store.myshopify.com` to any route that calls `shopify.authenticate.admin(request)`.
3. Observe (via `getSessionTokenContext` → `customAppSession`) that the library builds a `Session{shop: "attacker-owned-store.myshopify.com", accessToken: <real adminApiAccessToken>}` and that any subsequent `admin.graphql()`/REST call made in that request context is issued to `https://attacker-owned-store.myshopify.com/admin/api/...` carrying the real access token in the request header, fully attacker-directed with no prior verification.

### Citations

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts (L219-229)
```typescript

  const url = new URL(request.url);
  const shop = url.searchParams.get('shop')!;

  const sessionId = await api.session.getCurrentId({
    isOnline: config.useOnlineTokens,
    rawRequest: request,
  });

  return {shop, sessionId, payload: undefined, sessionToken};
}
```

**File:** packages/apps/shopify-app-remix/CHANGELOG.md (L976-996)
```markdown
- a84dadb: # Add support for merchant custom apps

  Merchant custom apps or apps that are distributed by the Shopify Admin are now supported.

  These apps do not Authorize by OAuth, and instead use a access token that has been generated by the Shopify Admin.

  Apps of this type are standalone apps and are not initiated from the Shopify Admin. Therefore it is **up to the developer of the app to add login and authentication functionality**.

  To use this library with Merchant Custom Apps set the following configuration in the `shopify.server` file:

  ```ts
  const shopify = shopifyApp({
    apiKey: "your-api-key",
    apiSecretKey: "your-api-secret-key",
    adminApiAccessToken:"shpat_1234567890",
    distribution: AppDistribution.ShopifyAdmin,
    appUrl: "https://localhost:3000",
    isEmbeddedApp: false,
  ```

  Session storage is _not_ required for merchant custom apps. A session is created from the provided access token.
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

**File:** packages/apps/shopify-api/lib/utils/shop-validator.ts (L11-50)
```typescript
export function sanitizeShop(config: ConfigInterface) {
  return (shop: string, throwOnInvalid = false): string | null => {
    let shopUrl = shop;
    const domainsRegex = [
      'myshopify\\.com',
      'shopify\\.com',
      'myshopify\\.io',
      'shop\\.dev',
    ];

    // Add domains from transformations (both source and target)
    if (config.domainTransformations) {
      domainsRegex.push(...getTransformationDomains(config));
    }

    const shopUrlRegex = new RegExp(
      `^[a-zA-Z0-9][a-zA-Z0-9-_]*\\.(${domainsRegex.join('|')})[/]*$`,
    );

    const shopAdminRegex = new RegExp(
      `^admin\\.(${domainsRegex.join('|')})/store/([a-zA-Z0-9][a-zA-Z0-9-_]*)$`,
    );

    const isShopAdminUrl = shopAdminRegex.test(shopUrl);
    if (isShopAdminUrl) {
      shopUrl = shopAdminUrlToLegacyUrl(shopUrl) || '';
    }

    const sanitizedShop = shopUrlRegex.test(shopUrl) ? shopUrl : null;
    if (!sanitizedShop && throwOnInvalid) {
      throw new InvalidShopError('Received invalid shop argument');
    }

    if (sanitizedShop && config.domainTransformations) {
      return applyDomainTransformations(sanitizedShop, config);
    }

    return sanitizedShop;
  };
}
```
