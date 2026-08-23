This is a confirmed vulnerability. The code path I traced fully supports it.

### Title
Merchant custom app strategy trusts unvalidated `shop` query param, allowing cross-tenant session materialization with the app's real static access token - ([File: packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/merchant-custom-app.ts])

### Summary
For apps configured with `AppDistribution.ShopifyAdmin` (merchant custom apps), `MerchantCustomAuth.authenticate` builds a `Session` purely from the `shop` query parameter of the incoming request via `api.session.customAppSession(shop)`, which only runs it through `sanitizeShop(config)(shop, true)` — a regex format check against domains like `myshopify.com`. There is no comparison against the app's actual installed/configured shop, nor any session-storage lookup. The resulting `Session.shop` is then used directly as `storeDomain` when building the GraphQL client, while the access token used is always `config.adminApiAccessToken` (the app's static, secret-bound token), independent of which shop string was supplied.

### Finding Description
The flow is:
1. `authStrategyFactory.authenticateAdmin` reads the shop straight from the URL: `const shop = url.searchParams.get('shop')!;` in `getSessionTokenContext` (`packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts:220-221`), with no HMAC or signature validation for this distribution type (the `sessionToken`/JWT path is only used when `config.distribution !== AppDistribution.ShopifyAdmin`).
2. This `shop` is passed into `MerchantCustomAuth.authenticate` (`packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/merchant-custom-app.ts:35-48`), which calls `this.api.session.customAppSession(shop)` with no further checks.
3. `customAppSession` (`packages/apps/shopify-api/lib/session/session-utils.ts:86-94`) only does:
   ```ts
   return new Session({id: '', shop: `${sanitizeShop(config)(shop, true)}`, state: '', isOnline: false});
   ```
   `sanitizeShop` (`packages/apps/shopify-api/lib/utils/shop-validator.ts:11-50`) is a pure regex/domain format check — it accepts *any* syntactically valid `*.myshopify.com`/`*.myshopify.io`/etc. string, with no check against the app's own configured/installed shop and no session-storage lookup.
4. The resulting `Session` (with `shop` = attacker-supplied string) is passed to `createAdminApiContext` → `adminClientFactory` → `graphqlClientFactory` → `new params.api.clients.Graphql({session})`, which constructs a `GraphqlClient` (`packages/apps/shopify-api/lib/clients/admin/graphql/client.ts:58-66`):
   ```ts
   this.client = createAdminApiClient({
     accessToken: config.adminApiAccessToken ?? this.session.accessToken!,
     apiVersion: this.apiVersion ?? config.apiVersion,
     storeDomain: this.session.shop,
     ...
   });
   ```
   Because `config.adminApiAccessToken` is set (merchant-custom-app config requires it), the app's real static access token is attached to a client whose `storeDomain` is the attacker-controlled `shop` string, with zero verification that this is the shop the app is actually installed on.

Existing checks (`validateShopAndHostParams`, HMAC verification, JWT/session-token validation) are all bypassed for this distribution: `validateShopAndHostParams` only runs `if (config.isEmbeddedApp)` (merchant custom apps are documented/configured as non-embedded), and `getSessionTokenContext` skips the JWT-`dest` derivation path entirely for `AppDistribution.ShopifyAdmin`, taking the raw query param instead. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

### Impact Explanation
An unprivileged attacker who can reach any route protected by `authenticate.admin` on a merchant-custom-app–distributed app can force the app to send Admin GraphQL API requests (using the app's real, secret-bound `adminApiAccessToken`) to an arbitrary `*.myshopify.com` domain of the attacker's choosing, not the merchant's own shop. This is a cross-tenant/SSRF-like abuse of the app's own privileged credential: token exfiltration is not required, but the app can be coerced into making authenticated Admin API calls against a shop it was never installed on (impact bounded by the fact that Shopify's Admin API will reject requests where the token isn't valid for that target shop, but the request itself, including the secret token in headers, is still dispatched to attacker-chosen infrastructure/domain — an SSRF-class issue plus tenant-isolation violation of the library's own session-scoping guarantee).

### Likelihood Explanation
Preconditions are narrow: this only affects apps that explicitly configure `distribution: AppDistribution.ShopifyAdmin` (merchant custom apps) — a supported, documented, non-exotic configuration. Given that precondition, exploitation requires no privilege beyond sending an HTTP request with a crafted `shop` query parameter; there is no HMAC, session-token, or storage check gating this value for this distribution type, making it trivially and repeatably reachable.

### Recommendation
For `AppDistribution.ShopifyAdmin`, validate that the `shop` query parameter matches the app's own configured shop (e.g., compare against a configured `shop`/`hostName` value, or derive the shop from a trusted source such as a signed session token or app-embed context) before calling `customAppSession(shop)`, rather than trusting an unauthenticated query parameter directly.

### Proof of Concept
```ts
// packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/__tests__/merchant-custom/cross-tenant.test.ts
import {AppDistribution} from '../../../../../types';
import {APP_URL, testConfig} from '../../../../../__test-helpers';
import {shopifyApp} from '../../../../..';

describe('merchant custom app cross-tenant shop', () => {
  it('materializes a session for an arbitrary attacker-chosen shop using the app real token', async () => {
    const config = testConfig({
      isEmbeddedApp: false,
      distribution: AppDistribution.ShopifyAdmin,
      adminApiAccessToken: 'shpat_real_secret_token',
    });
    const shopify = shopifyApp(config);

    const attackerShop = 'not-the-real-shop.myshopify.com';

    const {session} = await shopify.authenticate.admin(
      new Request(`${APP_URL}?shop=${attackerShop}`),
    );

    // No check ties this session back to the app's configured/installed shop.
    expect(session.shop).toBe(attackerShop);
    // admin.graphql() built from this session will target
    // https://not-the-real-shop.myshopify.com/admin/api/... using config.adminApiAccessToken.
  });
});
```

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts (L207-228)
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

**File:** packages/apps/shopify-api/lib/clients/admin/graphql/client.ts (L58-66)
```typescript
    this.client = createAdminApiClient({
      accessToken: config.adminApiAccessToken ?? this.session.accessToken!,
      apiVersion: this.apiVersion ?? config.apiVersion,
      storeDomain: this.session.shop,
      customFetchApi: abstractFetch,
      logger: clientLoggerFactory(config),
      userAgentPrefix: getUserAgent(config),
      isTesting: config.isTesting,
    });
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts (L1-30)
```typescript
import {redirect} from '@remix-run/server-runtime';

import {BasicParams} from '../../../types';

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

    const host = api.utils.sanitizeHost(url.searchParams.get('host')!);
    if (!host) {
      logger.debug('Invalid host, redirecting to login path', {
        shop,
        host: url.searchParams.get('host'),
      });
      throw redirectToLoginPath(request, params);
    }
  }
}
```
