Based on my investigation, I found a plausible analog, but I was unable to fully verify one critical link in the chain (the internal implementation of `getShopFromRequest` for the `ShopifyAdmin`/merchant-custom-app flow), so I present it with that caveat rather than asserting full certainty.

### Title
Merchant custom app admin sessions are built from an unverified request-derived `shop` value, which can misdirect the app's static admin access token to an attacker-controlled shop domain - (File: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/merchant-custom-app.ts` and `packages/apps/shopify-app-react-router/src/server/authenticate/admin/strategies/merchant-custom-app.ts`)

### Summary
For apps configured with `AppDistribution.ShopifyAdmin` (merchant custom/admin-created apps), `MerchantCustomAuth.authenticate()` builds an admin `Session` by calling `api.session.customAppSession(shop)` where `shop` comes from `getShopFromRequest(request)`. [1](#0-0)  `customAppSession` only calls `sanitizeShop(config)(shop, true)`, which validates that the string matches a generic `*.myshopify.com`/`*.myshopify.io`/`*.shop.dev` domain pattern - it does not verify that the value refers to the specific store the merchant actually installed the app on. [2](#0-1) [3](#0-2)  This is the same bug class as the Caviar `tokenURI` finding: the code accepts a caller-supplied identifier (an NFT id / a shop domain), performs only a superficial format check, and then returns/uses data (a session / metadata) as if the identifier were verified to correspond to a real, owned resource.

### Finding Description
`MerchantCustomAuth` explicitly skips OAuth for this app distribution mode (`respondToOAuthRequests` only logs and does nothing) because these apps are installed directly from the Shopify Admin using a merchant-configured static `adminApiAccessToken`, rather than going through the normal install/OAuth flow that binds a session to a specific, Shopify-verified shop. [4](#0-3)  Instead, the shop identity for the resulting `Session` comes straight from `getShopFromRequest(request)` and is passed to `customAppSession(shop)`, which only regex-validates the domain suffix. [3](#0-2)  The Admin API clients then construct the outbound request URL from `session.shop` while attaching the single, statically configured `adminApiAccessToken` credential as the auth header. [5](#0-4)  If `getShopFromRequest` returns a value derived from unauthenticated request input (e.g., a query parameter) rather than from a cryptographically verified source (such as a Shopify-signed session-token JWT `dest` claim, as used elsewhere in `getCurrentSessionId`), [6](#0-5)  an attacker could supply any syntactically valid `*.myshopify.com` domain (including one they own) and cause the app's admin credential to be sent to that attacker-controlled shop.

**I could not fully verify this last step** — I was unable to read the implementation of `getShopFromRequest` (`packages/apps/shopify-app-remix/src/server/authenticate/helpers/get-shop-from-request.ts`) within the available iterations to confirm whether, for the `ShopifyAdmin` distribution path specifically, it derives `shop` from an unauthenticated request field (query string) or from a verified source. This is the missing link required to fully confirm exploitability.

### Impact Explanation
If confirmed, this would allow leaking the merchant's static Admin API access token to an attacker-chosen `*.myshopify.com`/`*.myshopify.io` domain (a credential-disclosure / outbound-request-hijack primitive), analogous to how the Caviar `tokenURI` bug let an attacker make the contract return/act on data for an unverified, attacker-controlled identifier.

### Likelihood Explanation
Likelihood is uncertain pending confirmation of `getShopFromRequest`'s implementation for the merchant-custom-app path. If it trusts request-supplied shop values without cryptographic verification (unlike the JWT-based `dest` claim path used for embedded/token-exchange auth), likelihood would be high, since this app distribution mode explicitly bypasses OAuth-based shop binding.

### Recommendation
For the `AppDistribution.ShopifyAdmin` flow, verify that the `shop` value used to build the admin session and outbound API URL matches a shop identity established through a Shopify-signed mechanism (e.g., embedded App Bridge session token `dest` claim) rather than trusting a raw request field, and add an explicit check (mirroring the ERC721 "must be minted" fix) that the resolved shop is the one the app is actually configured/authorized for before dispatching requests with `adminApiAccessToken`.

### Proof of Concept
Not constructable without confirming the source of the `shop` value returned by `getShopFromRequest` for the `ShopifyAdmin` distribution path — this requires inspecting `get-shop-from-request.ts`, which I was unable to complete before running out of tool iterations.

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/merchant-custom-app.ts (L29-33)
```typescript
  public async respondToOAuthRequests(request: Request): Promise<void> {
    this.logger.debug('Skipping OAuth request for merchant custom app', {
      shop: getShopFromRequest(request),
    });
  }
```

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

**File:** packages/apps/shopify-api/lib/session/session-utils.ts (L42-64)
```typescript
      const authHeader = request.headers.Authorization;
      if (authHeader) {
        const matches = (
          typeof authHeader === 'string' ? authHeader : authHeader[0]
        ).match(/^Bearer (.+)$/);
        if (!matches) {
          log.error('Missing Bearer token in authorization header', {isOnline});

          throw new ShopifyErrors.MissingJwtTokenError(
            'Missing Bearer token in authorization header',
          );
        }

        const jwtPayload = await decodeSessionToken(config)(matches[1]);
        const shop = jwtPayload.dest.replace(/^https:\/\//, '');

        log.debug('Found valid JWT payload', {shop, isOnline});

        if (isOnline) {
          return getJwtSessionId(config)(shop, jwtPayload.sub);
        } else {
          return getOfflineId(config)(shop);
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

**File:** packages/apps/shopify-api/lib/utils/shop-validator.ts (L11-49)
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
```

**File:** packages/apps/shopify-api/lib/clients/admin/rest/client.ts (L1-1)
```typescript
import {
```
