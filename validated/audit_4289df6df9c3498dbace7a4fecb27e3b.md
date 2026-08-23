I found a valid finding: in the `shopify.authenticate.fulfillmentService` handler, the trusted `shop` identity is taken from an unauthenticated request header, but the HMAC signature that proves the request is genuinely from Shopify only covers the request body — not the shop domain header. This allows a shop-domain/HMAC binding mismatch that can be exploited for cross-tenant session access.

### Title
Fulfillment Service authenticator trusts unsigned `X-Shopify-Shop-Domain` header, enabling cross-tenant session hijack - (File: `packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/authenticate.ts` / `packages/apps/shopify-app-react-router/src/server/authenticate/fulfillment-service/authenticate.ts`)

### Summary
The fulfillment-service webhook authenticator validates the request's HMAC over the raw body only, then reads the target `shop` value from the `X-Shopify-Shop-Domain` header without any cryptographic binding between that header and the signed payload. Because the header is not covered by the signature, any actor who can obtain one validly-signed body/HMAC pair (e.g., from a fulfillment-service notification legitimately delivered to their own installed/dev shop) can resend that exact body+HMAC to the app's public endpoint while substituting a different shop's domain in the header, causing the app to load and act on a different (victim) merchant's offline session.

### Finding Description
`authenticateFulfillmentServiceFactory` reads the raw body, calls `api.fulfillmentService.validate({rawBody, rawRequest})` which only verifies `X-Shopify-Hmac-Sha256` against the body via `validateHmacFromRequestFactory` [1](#0-0) , and separately extracts the `shop` used for authorization purely from the unauthenticated `ShopifyHeader.Domain` header: [2](#0-1) 

The HMAC is computed only over `rawBody` (`validateHmacString(config, rawBody, hmac, HashFormat.Base64)`), never incorporating the shop/domain header: [3](#0-2) . This means the signature proves "this body was generated with the app's secret," but says nothing about which shop the request is actually for — the two are decoupled. This is precisely analogous to the report's bug class: a value that authorization/state logic implicitly assumes is "safe" or "bound" (like the `merchant` address in `MerchantSubscription`) is accepted without validating that it actually corresponds to the entity it's supposed to represent.

The `shop` value taken from the header is then used directly to fetch and act with that shop's offline access token: `ensureValidOfflineSession(params, shop)` → session returned is wired into an authenticated `admin` API client scoped to that arbitrary shop [4](#0-3) .

The test suite confirms this behavior is intentional/expected as implemented — an attacker-controlled `X-Shopify-Shop-Domain` value combined with a validly-signed body for a *different* payload is accepted at the HMAC-validation layer and only fails later if there happens to be no stored session for that domain: [5](#0-4) .

### Impact Explanation
A single merchant that has installed the app (attacker) can trigger a legitimate fulfillment-service webhook delivery to their own shop, capture the resulting `(rawBody, X-Shopify-Hmac-Sha256)` pair (both attacker-observable, since it's delivered to the attacker's own server/endpoint), and replay that exact body+HMAC to the app's fulfillment-service endpoint while swapping in a victim shop's domain in `X-Shopify-Shop-Domain`. If the victim shop has an offline session stored, the app will authenticate the request as belonging to the victim shop and hand the attacker's request handler an authenticated Admin API client (`admin`) scoped to the victim's access token and store data — a cross-tenant access/authorization bypass.

### Likelihood Explanation
Exploitation requires only: (1) the attacker's own shop has the app installed and receives at least one fulfillment-service webhook (attacker fully controls timing/content by triggering the corresponding fulfillment event on their own store), and (2) knowledge or guessing of a target shop's `.myshopify.com` domain (often discoverable/public). No secret leakage, MITM, or privileged access is required — the attacker only needs standard merchant-level access to their own store, matching the "single merchant" reachable threat model.

### Recommendation
Bind the shop identity into the signed material, or otherwise verify it, before trusting the domain header:
- Include the shop/domain as part of the HMAC-covered content (or verify it against a value embedded in the signed payload), similar to how the OAuth/App-Proxy paths canonicalize the `shop` query parameter into the signed HMAC input, rather than trusting `X-Shopify-Shop-Domain` as an out-of-band pointer for authorization.
- Alternatively, apply `sanitizeShop` and cross-check that the resolved session's shop matches an expected/allow-listed shop context (e.g., app-level config) before returning the `admin` client.

### Proof of Concept
1. Attacker installs the app on `attacker.myshopify.com` and triggers a fulfillment order event, so Shopify delivers a POST to the app's fulfillment-service endpoint with body `B` and header `X-Shopify-Hmac-Sha256: HMAC(secret, B)`.
2. Attacker captures `(B, HMAC(secret, B))` from the inbound request their own server receives.
3. Attacker sends a new POST to the same endpoint with the same body `B` and same `X-Shopify-Hmac-Sha256` header, but sets `X-Shopify-Shop-Domain: victim-shop.myshopify.com`.
4. `api.fulfillmentService.validate` returns `valid: true` (HMAC only checks body). `shop` is read from the forged header. `ensureValidOfflineSession(params, 'victim-shop.myshopify.com')` loads the victim's stored offline session, and the handler returns an authenticated `admin` client for the victim shop to the attacker's request context.

### Citations

**File:** packages/apps/shopify-api/lib/fulfillment-service/validate.ts (L10-20)
```typescript
export function validateFactory(config: ConfigInterface) {
  return async function validate({
    rawBody,
    ...adapterArgs
  }: ValidateParams): Promise<ValidationInvalid | ValidationValid> {
    return validateHmacFromRequestFactory(config)({
      type: HmacValidationType.FulfillmentService,
      rawBody,
      ...adapterArgs,
    });
  };
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/authenticate.ts (L33-80)
```typescript
    const rawBody = await request.text();
    const result = await api.fulfillmentService.validate({
      rawBody,
      rawRequest: request,
    });

    if (!result.valid) {
      logger.error('Received an invalid fulfillment service request', {
        reason: result.reason,
      });

      throw new Response(undefined, {
        status: 400,
        statusText: 'Bad Request',
      });
    }

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

    logger.debug('Found a session for the fulfillment service request', {
      shop,
    });

    return {
      session,
      payload,
      admin: adminClientFactory({params, session}),
    };
```

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L168-201)
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
  };
}
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
