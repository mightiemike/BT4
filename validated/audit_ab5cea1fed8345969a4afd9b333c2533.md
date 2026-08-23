### Title
Missing `X-Shopify-Shop-Domain` header causes uncaught `InvalidShopError` instead of a controlled 400 response - (File: packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/authenticate.ts)

### Summary
When a POST request to the fulfillment-service authenticate handler carries a valid HMAC over the body but omits (or empties) the `X-Shopify-Shop-Domain` header, `shop` is coerced to `''`. This empty string is passed to `ensureValidOfflineSession`, which eventually calls `sanitizeShop(config)('', true)`, which throws an uncaught `InvalidShopError` rather than returning `undefined`, so the handler never reaches its own `400` fallback and instead propagates an unhandled exception.

### Finding Description
In `authenticate` [1](#0-0) , after `api.fulfillmentService.validate` succeeds (HMAC-only check, independent of the shop header — see `validateHmacFromRequestFactory` [2](#0-1) ), the code does `const shop = request.headers.get(ShopifyHeader.Domain) || '';` and calls `ensureValidOfflineSession(params, shop)`.

`ensureValidOfflineSession` calls `createOrLoadOfflineSession` [3](#0-2) , which (for the default, non-`ShopifyAdmin` distribution) calls `api.session.getOfflineId(shop)`. That function is implemented as:
```
export function getOfflineId(config: ConfigInterface) {
  return (shop: string): string => {
    return `offline_${sanitizeShop(config)(shop, true)}`;
  };
}
``` [4](#0-3) 

`sanitizeShop(config)(shop, true)` with `throwOnInvalid = true` throws `InvalidShopError` when the regex fails to match — and an empty string never matches `^[a-zA-Z0-9][a-zA-Z0-9-_]*\.(myshopify\.com|...)$` [5](#0-4) . The same holds for the `AppDistribution.ShopifyAdmin` (merchant custom app) branch, which calls `customAppSession(shop)`, also invoking `sanitizeShop(config)(shop, true)` [6](#0-5) .

The `authenticate` function has no try/catch around `ensureValidOfflineSession` — it only checks `!session` after the `await` to produce the intended controlled `400` [7](#0-6) . Since `sanitizeShop` throws before returning, the `await` rejects with `InvalidShopError`, which propagates out of `authenticate` uncaught by this library code.

The existing test suite only exercises a *non-empty but unregistered* shop domain (`'not-a-real-shop.myshopify.com'`), which passes `sanitizeShop` validation and correctly hits the `!session` branch, returning 400 [8](#0-7) . There is no test covering a missing/empty shop header, so this uncaught-exception path is unverified and unguarded.

### Impact Explanation
An anonymous attacker who can forge a valid HMAC for a fulfillment-service payload (note: this requires knowledge of `apiSecretKey`-derived HMAC, see caveat below) but omits the shop header can crash the route handler with an unhandled `InvalidShopError` instead of receiving a clean `400`. Depending on the host framework's error boundary, this can surface as a `500` with a stack trace (information leak of internal paths/library internals) or an unhandled promise rejection (denial of service / process-level instability in non-Remix runtimes that don't sandbox thrown errors from loaders/actions). This violates the stated fail-closed invariant that all authentication failures in this handler return a controlled `400`/`405` `Response`.

### Likelihood Explanation
The preconditions materially limit real-world exploitability: `api.fulfillmentService.validate` requires a **valid HMAC computed with the app's `apiSecretKey`** over the raw body . An unprivileged external attacker without the app secret cannot produce a request that passes this HMAC check, since `validateHmacString` uses `safeCompare` against an HMAC keyed with the secret [9](#0-8) . Therefore, under the stated threat model (no leaked secret), this specific path is not reachable by a truly anonymous attacker — only Shopify itself (or someone possessing the secret) can produce a validly-HMAC'd request, and Shopify's genuine fulfillment-service calls always include the shop domain header. This reduces the practical likelihood to near zero for the "unprivileged attacker" threat model requested, though the code-level defect (missing try/catch, non-fail-closed on internal helper throwing) is real and reachable by a "confused deputy" or misconfigured caller.

### Recommendation
Wrap the `ensureValidOfflineSession(params, shop)` call in a `try/catch`, or pre-validate `shop` with `sanitizeShop(..., false)` (non-throwing) before calling `ensureValidOfflineSession`, returning the controlled `400` `Response` on empty/invalid shop, so that no internal validator (`sanitizeShop`, `InvalidShopError`) can escape as an unhandled exception from this authentication boundary.

### Proof of Concept
```ts
// packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/__tests__/authenticate.test.ts
it('returns a 400 (not a crash) when the shop domain header is missing', async () => {
  const shopify = shopifyApp(testConfig());
  const body = {kind: 'FULFILLMENT_REQUEST'};
  const bodyString = JSON.stringify(body);

  const request = new Request(FULFILLMENT_URL, {
    method: 'POST',
    body: bodyString,
    headers: {
      'X-Shopify-Hmac-Sha256': getHmac(bodyString), // valid HMAC, no shop header
    },
  });

  // Currently: this rejects with InvalidShopError instead of throwing a Response(400)
  const response = await getThrownResponse(
    shopify.authenticate.fulfillmentService,
    request,
  );

  expect(response.status).toBe(400);
  expect(response.statusText).toBe('Bad Request');
});
```
Given the current implementation, this test fails because `ensureValidOfflineSession` rejects with `InvalidShopError` before a `Response` is thrown, demonstrating the fail-open (uncaught exception) behavior instead of the expected fail-closed `400`.

**Caveat**: as noted above, triggering this in production additionally requires a valid HMAC signed with the app's `apiSecretKey`, which an unprivileged external attacker without that secret cannot forge — this limits real attacker reachability under the stated threat model even though the underlying code defect is confirmed.

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/authenticate.ts (L50-70)
```typescript
    const payload = JSON.parse(rawBody);
    const shop = request.headers.get(ShopifyHeader.Domain) || '';

    logger.debug(
      'Fulfillment service request is valid, looking for an offline session',
      {
        shop,
      },
    );

    const session = await ensureValidOfflineSession(params, shop);

    if (!session) {
      logger.info('Fulfillment service request could not find session', {
        shop,
      });
      throw new Response(undefined, {
        status: 400,
        statusText: 'Bad Request',
      });
    }
```

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L153-162)
```typescript
export async function validateHmacString(
  config: ConfigInterface,
  data: string,
  hmac: string,
  format: HashFormat,
) {
  const localHmac = await createSHA256HMAC(config.apiSecretKey, data, format);

  return safeCompare(hmac, localHmac);
}
```

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L168-199)
```typescript
export function validateHmacFromRequestFactory(config: ConfigInterface) {
  return async function validateHmacFromRequest({
    type,
    rawBody,
    webhookType,
    ...adapterArgs
  }: ValidateParams): Promise<ValidationInvalid | ValidationValid> {
    const request = await abstractConvertRequest(adapterArgs);
    if (!rawBody.length) {
      return fail(ValidationErrorReason.MissingBody, type, config);
    }

    // Use appropriate header based on webhook type
    const hmacHeaderName = webhookType
      ? WEBHOOK_HEADER_NAMES[webhookType].hmac
      : ShopifyHeader.Hmac;

    const hmac = getHeader(request.headers, hmacHeaderName);
    if (!hmac) {
      return fail(ValidationErrorReason.MissingHmac, type, config);
    }
    const validHmac = await validateHmacString(
      config,
      rawBody,
      hmac,
      HashFormat.Base64,
    );
    if (!validHmac) {
      return fail(ValidationErrorReason.InvalidHmac, type, config);
    }

    return succeed(type, config);
```

**File:** packages/apps/shopify-app-remix/src/server/helpers/create-or-load-offline-session.ts (L1-18)
```typescript
import {AppDistribution, BasicParams} from '../types';

export async function createOrLoadOfflineSession(
  {api, config, logger}: BasicParams,
  shop: string,
) {
  if (config.distribution === AppDistribution.ShopifyAdmin) {
    logger.debug('Creating custom app session from configured access token', {
      shop,
    });
    return api.session.customAppSession(shop);
  } else {
    logger.debug('Loading offline session from session storage', {shop});
    const offlineSessionId = api.session.getOfflineId(shop);
    const session = await config.sessionStorage!.loadSession(offlineSessionId);

    return session;
  }
```

**File:** packages/apps/shopify-api/lib/session/session-utils.ts (L22-26)
```typescript
export function getOfflineId(config: ConfigInterface) {
  return (shop: string): string => {
    return `offline_${sanitizeShop(config)(shop, true)}`;
  };
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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/__tests__/authenticate.test.ts (L80-101)
```typescript
    it('throws a 400 response if there is no session for the shop', async () => {
      // GIVEN
      const shopify = shopifyApp(testConfig());
      const body = {kind: 'FULFILLMENT_REQUEST'};

      // WHEN
      const response = await getThrownResponse(
        shopify.authenticate.fulfillmentService,
        new Request(FULFILLMENT_URL, {
          body: JSON.stringify(body),
          method: 'POST',
          headers: {
            'X-Shopify-Hmac-Sha256': getHmac(JSON.stringify(body)),
            'X-Shopify-Shop-Domain': 'not-a-real-shop.myshopify.com',
          },
        }),
      );

      // THEN
      expect(response.status).toBe(400);
      expect(response.statusText).toBe('Bad Request');
    });
```
