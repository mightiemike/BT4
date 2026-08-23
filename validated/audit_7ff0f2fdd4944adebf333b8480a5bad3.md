### Title
Signature type-confusion in `validateHmacFromRequestFactory` allows a Flow/Fulfillment-Service/Webhook HMAC to be replayed against a different validator - (File: `packages/apps/shopify-api/lib/utils/hmac-validator.ts`)

### Summary
`shopify.flow.validate`, `shopify.fulfillmentService.validate`, and `shopify.webhooks.validate` (for the traditional, non-events webhook type) all funnel into the same `validateHmacFromRequestFactory` helper. Each caller passes a `type` (`HmacValidationType.Flow`, `.FulfillmentService`, `.Webhook`) that is used only for log messages, never mixed into the signed data. All three read the identical header `ShopifyHeader.Hmac` (`X-Shopify-Hmac-Sha256`) and validate it with the exact same computation: `base64(HMAC-SHA256(apiSecretKey, rawBody))`. Because the "purpose" of the signature is not bound into what is signed or verified, a signature/body pair legitimately issued by Shopify for one call type is indistinguishable from — and will pass — validation for a totally different call type, mirroring the `refinanceFull`/`addNewTranche` signature-reuse issue where a signature intended for one action validated for another due to missing type separation.

### Finding Description
`validateHmacFromRequestFactory` accepts a `type: HmacValidationType` parameter but only uses it inside `fail()`/`succeed()` for debug logging: [1](#0-0) 

The actual cryptographic check is type-agnostic — it reads whatever header is chosen (`webhookType`-based header or the default `ShopifyHeader.Hmac`) and compares it to `base64(HMAC-SHA256(apiSecretKey, rawBody))`: [2](#0-1) 

Both `flow/validate.ts` and `fulfillment-service/validate.ts` call this same factory, differing only in the `type` tag that has no bearing on what is verified: [3](#0-2) [4](#0-3) 

`webhooks/validate.ts`'s traditional (non-events) path also reads the same `ShopifyHeader.Hmac` header via `validateHmacFromRequestFactory`: [5](#0-4) 

`ShopifyHeader.Hmac` is a single shared constant (`X-Shopify-Hmac-Sha256`) used across all three call sites: [6](#0-5) 

Consequently there is no "type" bound into the signed payload (analogous to the missing type field the C4 report recommends adding to `RenegotiationOffer`). Any raw request body plus its valid HMAC — regardless of which Shopify subsystem produced it (Flow action, Fulfillment Service callback, or a traditional webhook) — will validate successfully at any endpoint in the app that calls one of these three `validate()` functions, as long as the app developer wires the same body/header pair to the wrong handler or an attacker replays a captured body+HMAC pair to a different endpoint of the same app that happens to accept overlapping JSON shapes.

### Impact Explanation
If an app exposes both, say, a Flow action endpoint and a Fulfillment Service callback endpoint (or a generic webhook endpoint) using this library's respective `validate()` helpers, a captured (non-secret, transmitted over HTTP to the merchant's own app) body+HMAC pair from one endpoint can be replayed to the other endpoint and will pass HMAC validation, since the verification is identical and carries no endpoint/purpose binding. Depending on how the app's route handler subsequently parses/acts on the JSON body (fulfillment order updates vs. Flow action execution vs. generic webhook processing), this can cause the app to process attacker-chosen data as if it came from the intended, distinct Shopify subsystem — a cross-context request forgery / type confusion within the app's own trusted-request boundary, directly analogous to the reused-signature root cause in the reference finding.

### Likelihood Explanation
Reachable from any external actor who can capture or otherwise obtain a valid `(rawBody, X-Shopify-Hmac-Sha256)` pair sent by Shopify to one of the app's public endpoints, then replay it against a different endpoint of the same app; no privileged access or secret leakage is required beyond intercepting/replaying data the app itself receives over unauthenticated HTTP webhook endpoints. Exploitability further depends on the specific app's routing and whether the JSON schemas of Flow/Fulfillment-Service/Webhook payloads overlap enough for the receiving handler to accept the replayed body, which the shopify-app-js library itself cannot control — this is a design gap in the validators rather than a guaranteed exploit in every app.

### Recommendation
Bind the `type`/purpose into what is actually verified rather than just into log messages: either (a) include a fixed context string (e.g., `"flow"`, `"fulfillment_service"`, `"webhook"`) as part of the HMAC input alongside `rawBody`, or (b) require/verify a header or field that Shopify sets uniquely per subsystem (e.g., a required `X-Shopify-Topic`/API path check) as part of the trust decision, so a valid signature for one call type cannot be replayed against another `validate()` entry point.

### Proof of Concept
1. App registers a Flow action handler at `/webhooks/flow` using `shopify.flow.validate()` and a Fulfillment Service callback handler at `/webhooks/fulfillment` using `shopify.fulfillmentService.validate()`.
2. Shopify sends a legitimate Flow action POST with body `B` and header `X-Shopify-Hmac-Sha256: H`, where `H = base64(HMAC-SHA256(secret, B))`.
3. An attacker (or a compromised intermediary/replay from the app's own logs/queue) resends the exact same `(B, H)` pair to `/webhooks/fulfillment`.
4. `fulfillmentService.validate()` calls `validateHmacFromRequestFactory` with `type: HmacValidationType.FulfillmentService`, reads the same `X-Shopify-Hmac-Sha256` header, computes `base64(HMAC-SHA256(secret, B))`, which equals `H`, and returns `valid: true`, even though this body was never actually generated for the Fulfillment Service subsystem — confirming the code path shown in [1](#0-0)  performs no type-specific binding.

### Citations

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

**File:** packages/apps/shopify-api/lib/flow/validate.ts (L10-21)
```typescript
export function validateFactory(config: ConfigInterface) {
  return async function validate({
    rawBody,
    ...adapterArgs
  }: ValidateParams): Promise<ValidationInvalid | ValidationValid> {
    return validateHmacFromRequestFactory(config)({
      type: HmacValidationType.Flow,
      rawBody,
      ...adapterArgs,
    });
  };
}
```

**File:** packages/apps/shopify-api/lib/fulfillment-service/validate.ts (L10-21)
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
}
```

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L26-61)
```typescript
function detectWebhookType(headers: Headers): WebhookTypeValue {
  const eventsHmac = getHeader(
    headers,
    WEBHOOK_HEADER_NAMES[WebhookType.Events].hmac,
  );
  if (eventsHmac) {
    return WebhookType.Events;
  }

  const webhooksHmac = getHeader(
    headers,
    WEBHOOK_HEADER_NAMES[WebhookType.Webhooks].hmac,
  );
  if (webhooksHmac) {
    return WebhookType.Webhooks;
  }

  return WebhookType.Webhooks;
}

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
```

**File:** packages/apps/shopify-api/lib/types.ts (L24-37)
```typescript
export enum ShopifyHeader {
  AccessToken = 'X-Shopify-Access-Token',
  ApiVersion = 'X-Shopify-API-Version',
  Domain = 'X-Shopify-Shop-Domain',
  Hmac = 'X-Shopify-Hmac-Sha256',
  Topic = 'X-Shopify-Topic',
  WebhookId = 'X-Shopify-Webhook-Id',
  Name = 'X-Shopify-Name',
  TriggeredAt = 'X-Shopify-Triggered-At',
  EventId = 'X-Shopify-Event-Id',
  StorefrontPrivateToken = 'Shopify-Storefront-Private-Token',
  StorefrontSDKVariant = 'X-SDK-Variant',
  StorefrontSDKVersion = 'X-SDK-Version',
}
```
