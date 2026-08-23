This confirms the analog: the `X-Shopify-Shop-Domain` header used to select which shop's session gets loaded is **not covered by the HMAC signature** — the HMAC only signs the raw body — yet that header value is trusted to determine which merchant's offline access token/session is handed to the request handler. [1](#0-0) [2](#0-1) [3](#0-2) 

This is structurally identical to the reported bug class: `validateHmacFromRequestFactory` (the "message sender" check) verifies only the raw body against `X-Shopify-Hmac-Sha256`, but it never authenticates the "channel" — i.e., the `X-Shopify-Shop-Domain` header — the same class of gap the Allora report calls out (`OnRecvPacket` validates sender but not `channel-id`).

### Title
Fulfillment Service Authentication Trusts Unsigned `X-Shopify-Shop-Domain` Header to Select Session - ([File: packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/authenticate.ts])

### Summary
`authenticateFulfillmentServiceFactory` validates the HMAC of the raw request body via `api.fulfillmentService.validate`, but the shop identity used to look up and hand back an offline session/admin client is read directly from the `X-Shopify-Shop-Domain` request header, which is not covered by that HMAC signature at all.

### Finding Description
`validateFactory` in `packages/apps/shopify-api/lib/fulfillment-service/validate.ts` delegates to `validateHmacFromRequestFactory`, which computes the HMAC exclusively over `rawBody` [4](#0-3) . Headers such as `X-Shopify-Shop-Domain` are never part of the signed material. After validation succeeds, `authenticateFulfillmentServiceFactory` reads the shop directly from that unauthenticated header (`request.headers.get(ShopifyHeader.Domain)`) and uses it to call `ensureValidOfflineSession`, which loads that shop's stored offline access token and constructs an authenticated `admin` GraphQL client for the caller [5](#0-4) . The same pattern (HMAC-over-body only, shop/domain header trusted separately) is confirmed by the fulfillment-service test suite, which sets `X-Shopify-Shop-Domain` as a plain, unsigned header alongside the body HMAC [6](#0-5) .

### Impact Explanation
Whoever can produce a request with any valid HMAC for a chosen body (this requires knowing the app's shared secret, as with legitimate Shopify fulfillment callbacks — the same trust boundary Shopify's own webhook infrastructure relies on) can additionally set `X-Shopify-Shop-Domain` to a value belonging to a **different installed shop**, and the library will hand back a working admin API client authenticated for that unrelated shop. That is a channel/tenant confusion: the sender's HMAC only proves the party sent *some* valid signed body, it says nothing about which shop's session should be returned. This is the exact bug class in the report — verifying the message but not the channel identifier used for routing/authorization.

### Likelihood Explanation
Exploitability depends on whether an attacker (or, more concerning, a compromised/malicious app on a shared endpoint, or replay of a legitimate one-shop request against a different `Domain` header) can supply an arbitrary `X-Shopify-Shop-Domain` while still producing a valid HMAC over the body — which they can, since the domain header is never included in the signed data. Because most fulfillment/webhook infrastructure treats domain/topic headers as informational metadata alongside a body-only signature (mirroring the actual Shopify platform's design), this is somewhat mitigated by the fact that the HMAC secret itself is required, limiting it to parties who already have signing capability but could still target a different shop than the one for which they were authorized.

### Recommendation
Bind the shop domain into the authenticated data path: either include the `X-Shopify-Shop-Domain` header content in the HMAC computation, or independently verify (e.g., against Shopify Admin API/`shop` GraphQL query) that the resolved session's shop matches an expected/allow-listed value before returning the `admin` client, rather than trusting the header value outright to select which shop's offline token to use.

### Proof of Concept
1. Obtain a body and its corresponding valid `X-Shopify-Hmac-Sha256` HMAC (possible for anyone able to produce Shopify-format signed payloads, e.g. a party that received one legitimate signed callback for their own shop).
2. Send a POST to the fulfillment-service authentication endpoint with that valid HMAC/body pair, but set `X-Shopify-Shop-Domain` to a different, victim shop domain that also has an app installation.
3. `authenticateFulfillmentServiceFactory` accepts the HMAC as valid (it only checks the body) and calls `ensureValidOfflineSession(params, victim-shop)`, returning a working `admin` client scoped to the victim shop's stored offline access token [7](#0-6) .

### Citations

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

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L168-200)
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
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/__tests__/authenticate.test.ts (L174-192)
```typescript
async function getValidRequest(sessionStorage: SessionStorage) {
  const session = await setUpValidSession(sessionStorage, {
    isOnline: false,
  });

  const body = {kind: 'FULFILLMENT_REQUEST'};
  const bodyString = JSON.stringify(body);

  const request = new Request(FULFILLMENT_URL, {
    body: bodyString,
    method: 'POST',
    headers: {
      'X-Shopify-Hmac-Sha256': getHmac(bodyString),
      'X-Shopify-Shop-Domain': TEST_SHOP,
    },
  });

  return {body, request, session};
}
```
