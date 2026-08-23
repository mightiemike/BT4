Confirmed. The webhook HMAC (`X-Shopify-Hmac-Sha256`) only signs `rawBody`, and the `X-Shopify-Shop-Domain`/`X-Shopify-Topic`/`X-Shopify-Webhook-Id` headers are read out-of-band and never included in the signed material.

### Title
Webhook HMAC does not cover the shop-domain header, enabling cross-tenant webhook forgery - (File: packages/apps/shopify-api/lib/webhooks/validate.ts)

### Summary
Shopify webhook validation (`validateFactory` → `validateHmacFromRequestFactory`) computes and checks the HMAC exclusively over `rawBody` [1](#0-0) . The shop-identifying header (`X-Shopify-Shop-Domain`, exposed as `check.domain`) is extracted separately by `checkWebhookHeaders` after HMAC validation succeeds and is never part of the signed payload [2](#0-1) [3](#0-2) . Consuming code (e.g. `authenticateWebhookFactory` in shopify-app-remix / shopify-app-react-router) trusts `check.domain` to fetch the corresponding shop's stored offline session and to build an authenticated admin client for that shop [4](#0-3) .

### Finding Description
This is the same bug class as the reported Solana issue: a value used later for a security-critical decision (there, `effective_token_from_amount`; here, the shop/tenant identity) is excluded from the data actually covered by the signature. Because Shopify signs webhooks with the app's single `apiSecretKey` — shared across every installed shop, not a per-shop secret — any actor who can install the app on their own shop (a normal, unprivileged merchant/customer action) can receive genuine webhook deliveries with a valid `rawBody` + `X-Shopify-Hmac-Sha256` pair for their own shop. Since the signature never covers the `X-Shopify-Shop-Domain` header, that exact valid `(rawBody, hmac)` pair can be replayed to the app's webhook endpoint with the domain header rewritten to name a *different, victim* shop. `validateHmacFromRequestFactory` will report the request as valid (it only rechecks the body/HMAC pair) [5](#0-4) , and `checkWebhookHeaders` will report the attacker-chosen domain as `check.domain`. Downstream webhook authentication then resolves and injects the *victim's* offline session/access token into the handler context, while the payload content is attacker-controlled (it was their own legitimately-signed body) [6](#0-5) .

### Impact Explanation
An unprivileged actor (any merchant who can install the app) can cause the webhook pipeline to execute app business logic against a victim shop's session and admin API credentials while supplying data of their choosing (subject to matching a real webhook topic/body shape they can generate themselves, e.g. `products/create` on their own store). This is cross-tenant request forgery: the app cannot distinguish "genuine Shopify webhook about shop X" from "genuine Shopify webhook about shop Y, replayed with a forged domain header." Depending on what webhook handlers do (write side effects, trigger emails, mutate app data, revoke resources, etc.), this can cause data corruption, unauthorized actions performed with another shop's credentials, or a targeted denial of service against a specific victim shop by flooding it with bogus event processing.

### Likelihood Explanation
Exploitation requires only an app installation on any shop the attacker controls (trivial for public/free apps and easily achievable for private apps too) plus interception/capture of one legitimate webhook delivery to that shop (webhooks are delivered over plain HTTP(S) to the developer's own endpoint, and the attacker fully controls that endpoint) and the ability to send a request to the app's webhook URL with a modified header — no cryptographic secret is required to be leaked. This makes likelihood high for any Shopify app relying on `shopify.webhooks.validate` and this authentication flow without additional out-of-band tenant binding.

### Recommendation
Bind the shop identity into the security decision, not just into an unauthenticated header:
- Preferentially resolve the shop from data that Shopify already signs or that is otherwise cryptographically bound to the tenant (e.g., cross-check `check.domain` against the session and reject if the webhook subscription's registered shop doesn't match, or use per-shop webhook secrets where the platform supports them).
- At minimum, when using `check.domain` to select a session, verify that the resolved session's `shop` was actually the target of this specific webhook delivery via additional Shopify-side verification (e.g., validating `webhookId` uniqueness/idempotency scoped per shop, since webhook IDs are unique per subscription/shop) and reject deliveries whose `domain` doesn't correspond to an actual active subscription for that topic from that shop.
- Track processed `webhookId` values per shop to reject/no-op duplicate or cross-shop replays.

### Proof of Concept
1. Attacker installs the target app on `attacker-shop.myshopify.com` and triggers a webhook event (e.g., creates a product to fire `products/create`). They receive the raw POST body `B` and header `X-Shopify-Hmac-Sha256: H`, which is valid because `H = HMAC(apiSecretKey, B)`.
2. Attacker sends `POST {app webhook endpoint}` with body `B`, header `X-Shopify-Hmac-Sha256: H` (unchanged, still valid since `B` is unchanged), but with `X-Shopify-Shop-Domain: victim-shop.myshopify.com` instead of their own domain.
3. `validateHmacFromRequestFactory` validates `H` against `B` successfully (domain is not part of the HMAC input) [7](#0-6) .
4. `checkWebhookHeaders` reports `domain: 'victim-shop.myshopify.com'`.
5. `authenticateWebhookFactory` calls `ensureValidOfflineSession(params, 'victim-shop.myshopify.com')`, obtaining the victim's stored offline access token, and invokes the app's webhook handler with `session` bound to the victim shop and `payload` equal to the attacker's own controlled body `B` [6](#0-5) .

### Citations

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L185-197)
```typescript
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
```

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L46-73)
```typescript
export function validateFactory(config: ConfigInterface) {
  return async function validate({
    rawBody,
    ...adapterArgs
  }: WebhookValidateParams): Promise<WebhookValidation> {
    const request: NormalizedRequest =
      await abstractConvertRequest(adapterArgs);

    const webhookType = detectWebhookType(request.headers);

    const validHmacResult = await validateHmacFromRequestFactory(config)({
      type: HmacValidationType.Webhook,
      rawBody,
      webhookType,
      ...adapterArgs,
    });

    if (!validHmacResult.valid) {
      if (validHmacResult.reason === ValidationErrorReason.InvalidHmac) {
        const log = logger(config);
        await log.debug(
          "Webhook HMAC validation failed. Please note that events manually triggered from a store's Notifications settings will fail this validation. To test this, please use the CLI or trigger the actual event in a development store.",
        );
      }
      return validHmacResult;
    }

    return checkWebhookHeaders(request.headers, webhookType);
```

**File:** packages/apps/shopify-api/lib/webhooks/types.ts (L13-36)
```typescript
export const WEBHOOK_HEADER_NAMES = {
  [WebhookType.Webhooks]: {
    hmac: ShopifyHeader.Hmac,
    topic: ShopifyHeader.Topic,
    domain: ShopifyHeader.Domain,
    apiVersion: ShopifyHeader.ApiVersion,
    webhookId: ShopifyHeader.WebhookId,
    name: ShopifyHeader.Name,
    triggeredAt: ShopifyHeader.TriggeredAt,
    eventId: ShopifyHeader.EventId,
  },
  [WebhookType.Events]: {
    hmac: ShopifyEventsHeader.Hmac,
    topic: ShopifyEventsHeader.Topic,
    domain: ShopifyEventsHeader.Domain,
    apiVersion: ShopifyEventsHeader.ApiVersion,
    webhookId: ShopifyEventsHeader.WebhookId,
    eventId: ShopifyEventsHeader.EventId,
    handle: ShopifyEventsHeader.Handle,
    action: ShopifyEventsHeader.Action,
    resourceId: ShopifyEventsHeader.ResourceId,
    triggeredAt: ShopifyEventsHeader.TriggeredAt,
  },
} as const;
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L40-102)
```typescript
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

    let webhookContext: WebhookContextWithoutSession<Topics>;

    if (check.webhookType === WebhookType.Webhooks) {
      webhookContext = {
        apiVersion: check.apiVersion,
        shop: check.domain,
        topic: check.topic as Topics,
        webhookId: check.webhookId,
        payload: JSON.parse(rawBody),
        session: undefined,
        admin: undefined,
        webhookType: check.webhookType,
        name: check.name,
        triggeredAt: check.triggeredAt,
        eventId: check.eventId,
      };
    } else {
      webhookContext = {
        apiVersion: check.apiVersion,
        shop: check.domain,
        topic: check.topic as Topics,
        webhookId: check.webhookId,
        payload: JSON.parse(rawBody),
        session: undefined,
        admin: undefined,
        webhookType: check.webhookType,
        handle: check.handle,
        action: check.action,
        resourceId: check.resourceId,
        triggeredAt: check.triggeredAt,
        eventId: check.eventId,
      };
    }

    if (!session) {
      return webhookContext;
    }

    const admin = adminClientFactory({
      params,
      session,
      handleClientError: handleClientErrorFactory({request}),
    });

    return {
      ...webhookContext,
      session,
      admin,
    };
```
