This confirms the analog. `check.domain` is derived purely from the `X-Shopify-Shop-Domain` header (or `X-Shopify-Events-Shop-Domain` for events webhooks) via `checkWebhooksHeaders`/`checkEventsHeaders` in `packages/apps/shopify-api/lib/webhooks/validate.ts`, and it is never covered by the HMAC computation, which is performed solely over `rawBody` in `validateHmacFromRequestFactory` (`packages/apps/shopify-api/lib/utils/hmac-validator.ts:168-201`). This `check.domain` value is then trusted directly to look up and load the target shop's offline session (`ensureValidOfflineSession(params, check.domain)`), exactly mirroring the reported bug where a signature doesn't bind the target identifier (`fid`), enabling it to be misapplied to a different one.

### Title
Webhook HMAC signature does not bind the claimed shop domain/topic/webhookId, enabling cross-tenant webhook forgery - (File: packages/apps/shopify-api/lib/utils/hmac-validator.ts)

### Summary
Shopify webhook authentication verifies only that `X-Shopify-Hmac-Sha256` matches an HMAC-SHA256 of the raw request body using the app's shared API secret. The `X-Shopify-Shop-Domain`, `X-Shopify-Topic`, and `X-Shopify-Webhook-Id` headers (or their Events-webhook equivalents) are read directly from the request and are never included in the signed content. Any party who can obtain one valid `(rawBody, hmac)` pair signed with the app's secret can resend it to the app's webhook endpoint with an arbitrary shop-domain header, causing the framework to load and act on a different (victim) shop's offline session.

### Finding Description
`validateHmacFromRequestFactory` in [1](#0-0)  computes the expected HMAC using only `rawBody` and compares it to the `hmac` header value; no other request field participates in the digest. Meanwhile, `checkWebhooksHeaders`/`checkEventsHeaders` in [2](#0-1)  extract `domain`, `topic`, and `webhookId` straight from request headers with no cryptographic link to the HMAC-verified body. `validateFactory` in the same file only gates on `validHmacResult.valid` before returning these unauthenticated header values as trusted (`domain`, `topic`, etc.), as seen at [3](#0-2) .

Downstream, `authenticateWebhookFactory` (identical in shopify-app-remix and shopify-app-react-router) uses this unauthenticated `check.domain` directly to select which shop's offline session to load and inject into the handler/`admin` client: [4](#0-3) . Since every shop that installs the app shares the same `apiSecretKey` (the HMAC key is app-level, not per-shop), an HMAC that is valid for one shop's webhook body is equally valid when replayed with a forged `X-Shopify-Shop-Domain` header naming a different shop — this is the direct analog of the `KeyRegistry.removeFor()` issue, where a signature omits the target identifier (`fid`) and can be misapplied to an unintended target.

### Impact Explanation
An attacker who is a merchant with the app installed on their own shop can obtain legitimately-Shopify-signed `(rawBody, hmac)` pairs for topics whose payloads include attacker-controlled content (many webhook topics embed merchant-supplied fields such as product/customer/order metafields, notes, or titles). By resending that exact body/HMAC pair directly to the app's public webhook endpoint while forging `X-Shopify-Shop-Domain` (and freely choosing `X-Shopify-Topic`/`X-Shopify-Webhook-Id`), the attacker can make the app process an arbitrary webhook topic "as if" it came from any other shop that has installed the app, with the victim's offline session (and thus `admin` API access token) attached to the handler. This is a cross-tenant forged-request vulnerability that can trigger business logic reserved for another merchant's data.

### Likelihood Explanation
Reachable from an anonymous HTTP POST to the app's public webhook endpoint — no privileged access is required beyond installing the app once as any ordinary merchant to harvest a signed payload. The shared app-level secret across all shops and the header-only trust for domain/topic make this straightforward to exploit once a signed body is obtained.

### Recommendation
Include the shop domain (and preferably topic/webhookId) as part of the HMAC-covered material, or otherwise cryptographically bind the header-derived `domain`/`topic` to the verified body (e.g., mixing them into the HMAC input, or requiring Shopify's webhook subscription per-shop secrets if available). At minimum, cross-check that the claimed `domain` header corresponds to a shop for which this exact `(rawBody, hmac)` pair could only have been generated (not simply "any shop using the same app secret").

### Proof of Concept
1. Install the app on attacker-owned shop `attacker.myshopify.com`; trigger a webhook topic whose body includes attacker-chosen content (e.g. `PRODUCTS_UPDATE` with a crafted product title/metafield), and capture the resulting `rawBody` and `X-Shopify-Hmac-Sha256` value delivered by Shopify.
2. POST that exact `rawBody` to the app's webhook endpoint, but replace headers: `X-Shopify-Shop-Domain: victim.myshopify.com`, `X-Shopify-Topic: <any registered topic>`, `X-Shopify-Webhook-Id: <arbitrary>`, keeping the captured `X-Shopify-Hmac-Sha256`.
3. `api.webhooks.validate()` returns `valid: true` because only `rawBody` is checked against the HMAC (`hmac-validator.ts` lines 189-197); `checkWebhooksHeaders` returns `domain: 'victim.myshopify.com'` unchecked.
4. `authenticateWebhookFactory` calls `ensureValidOfflineSession(params, 'victim.myshopify.com')`, loading the victim's offline session/access token and passing it to the app's webhook handler under the attacker-chosen topic, despite the request never having been sent by Shopify for that shop.

### Citations

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

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L46-74)
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
  };
```

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L99-146)
```typescript
function checkWebhooksHeaders(
  headers: Headers,
): WebhookValidationMissingHeaders | WebhookValidationValid {
  const headerNames = WEBHOOK_HEADER_NAMES[WebhookType.Webhooks];
  const missingHeaders: string[] = [];

  const hmac = getRequiredHeader(headers, headerNames.hmac, missingHeaders);
  const topic = getRequiredHeader(headers, headerNames.topic, missingHeaders);
  const domain = getRequiredHeader(headers, headerNames.domain, missingHeaders);
  const apiVersion = getRequiredHeader(
    headers,
    headerNames.apiVersion,
    missingHeaders,
  );
  const webhookId = getRequiredHeader(
    headers,
    headerNames.webhookId,
    missingHeaders,
  );

  if (missingHeaders.length) {
    return {
      valid: false,
      reason: WebhookValidationErrorReason.MissingHeaders,
      missingHeaders,
    };
  }

  const fields: WebhooksWebhookFields = {
    webhookType: WebhookType.Webhooks,
    hmac: hmac!,
    topic: topicForStorage(topic!),
    domain: domain!,
    apiVersion: apiVersion!,
    webhookId: webhookId!,
  };

  const name = getHeader(headers, headerNames.name);
  if (name) fields.name = name;

  const triggeredAt = getHeader(headers, headerNames.triggeredAt);
  if (triggeredAt) fields.triggeredAt = triggeredAt;

  const eventId = getHeader(headers, headerNames.eventId);
  if (eventId) fields.eventId = eventId;

  return {valid: true, ...fields};
}
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L35-61)
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

    let webhookContext: WebhookContextWithoutSession<Topics>;

    if (check.webhookType === WebhookType.Webhooks) {
      webhookContext = {
        apiVersion: check.apiVersion,
        shop: check.domain,
        topic: check.topic as Topics,
        webhookId: check.webhookId,
```
