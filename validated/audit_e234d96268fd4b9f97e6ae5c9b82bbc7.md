## Analysis

This confirms the vulnerability class: the fulfillment-service HMAC only signs `rawBody` (via `validateHmacFromRequestFactory`), and never binds the `X-Shopify-Shop-Domain` header to the signature. [1](#0-0)  The HMAC secret (`apiSecretKey`) is a single app-wide secret shared across every shop that has installed the app, not a per-shop secret. [2](#0-1)  After validation succeeds, the shop identity used to look up (and act on behalf of) a session is taken directly, unauthenticated, from the `X-Shopify-Shop-Domain` request header:

```ts
const shop = request.headers.get(ShopifyHeader.Domain) || '';
...
const session = await ensureValidOfflineSession(params, shop);
``` [3](#0-2) 

This mirrors the "no checks on externally-supplied identifying data used for a security decision" bug class from the report: just as the LST oracle trusted an unchecked, single-controller-supplied rate to drive protocol logic, this handler trusts an unchecked, attacker-controlled `shop` header (not covered by the signature) to select which tenant's offline access token gets used to build the admin API client.

### Title
Fulfillment-service webhook HMAC does not bind the shop domain, enabling cross-tenant session use - (File: packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/authenticate.ts)

### Summary
The `authenticate.fulfillmentService` handler validates only the HMAC over the raw body, then reads the target shop from the unauthenticated `X-Shopify-Shop-Domain` header and uses it to load that shop's offline session/access token.

### Finding Description
`validateFactory` in `fulfillment-service/validate.ts` calls `validateHmacFromRequestFactory`, which computes the HMAC solely over `rawBody` and compares it against the `X-Shopify-Hmac-Sha256` header [4](#0-3) . The shop domain header is never included in the signed data. Because `apiSecretKey` is one shared secret for the whole app across all installed shops [5](#0-4) , any entity able to produce a validly-signed body for shop A's payload (e.g., a merchant/customer of a shop that has installed the app, who can trigger real fulfillment-order-notification callbacks, or replay a captured one) can pair that identical body+HMAC with a forged `X-Shopify-Shop-Domain: victim-shop.myshopify.com` header. `authenticateFulfillmentServiceFactory` then trusts this header verbatim as `shop` and passes it straight into `ensureValidOfflineSession`, which loads victim-shop's offline session/access token and constructs an authenticated admin API client bound to it [6](#0-5) . There is no sanitization (`sanitizeShop`) or cross-check that the domain header corresponds to the authenticated request the way `webhooks.validate` binds `check.domain` from its own signed header set for standard webhooks [7](#0-6) .

### Impact Explanation
If exploited, an attacker with the ability to produce one valid `(body, HMAC)` pair for the app (any installer, or capture-and-replay of a legitimate fulfillment-service callback) can direct the app to load and use a *different* shop's offline access token, returning that shop's payload/session/admin client context to the attacker's controlled webhook handler code path. This is a cross-tenant access primitive against the app's own persisted access tokens.

### Likelihood Explanation
Requires the attacker to already have a way to obtain one valid signed body/HMAC pair (e.g., their own installed shop generating real fulfillment-service traffic, or captured replay of a real notification), then resend it with a spoofed shop-domain header — no secret leakage or privileged access is needed beyond normal app installation/traffic interception. This is a plausible, low-effort action for any anonymous/self-installed merchant.

### Recommendation
Bind the shop domain (and other identifying headers) into the HMAC-signed payload, or independently verify that the value in `X-Shopify-Shop-Domain` corresponds to the shop that legitimately produced the signed body (e.g., derive shop identity from a signed/opaque token rather than a raw header), consistent with how `webhooks.validate` treats `domain` as part of its authenticated result.

### Proof of Concept
1. Attacker installs the app on `attacker-shop.myshopify.com` and triggers a real fulfillment-order-notification, capturing `body` and its valid `X-Shopify-Hmac-Sha256` value.
2. Attacker resends the identical `body` and `X-Shopify-Hmac-Sha256` to the app's fulfillment-service endpoint, replacing `X-Shopify-Shop-Domain` with `victim-shop.myshopify.com`.
3. `validateFactory` validates the HMAC (computed only over `body`) successfully [1](#0-0) .
4. `authenticateFulfillmentServiceFactory` reads `shop = 'victim-shop.myshopify.com'` from the header and loads victim-shop's offline session/access token, exposing it to the attacker's handler logic [6](#0-5) .

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

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L153-200)
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

export function getCurrentTimeInSec() {
  return Math.trunc(Date.now() / 1000);
}

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L35-52)
```typescript
    const check = await api.webhooks.validate({
      rawBody,
      rawRequest: request,
    });

    if (!check.valid) {
      if (check.reason === WebhookValidationErrorReason.InvalidHmac) {
        logger.debug('Webhook HMAC validation failed', check);
        throw new Response(undefined, {
          status: 401,
          statusText: 'Unauthorized',
        });
      } else {
        logger.debug('Webhook validation failed', check);
        throw new Response(undefined, {status: 400, statusText: 'Bad Request'});
      }
    }
    const session = await ensureValidOfflineSession(params, check.domain);
```
