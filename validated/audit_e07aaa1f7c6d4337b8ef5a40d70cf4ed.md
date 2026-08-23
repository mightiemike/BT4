### Title
Webhook HMAC Validation Has No Timestamp/Freshness Check, Allowing Indefinite Replay of Captured Webhook Requests - (File: `packages/apps/shopify-api/lib/utils/hmac-validator.ts`)

### Summary
The webhook validation path (`shopify.webhooks.validate` / `webhooks.process`) verifies the HMAC signature of a webhook's raw body but never checks the freshness of the request, unlike the OAuth query-string HMAC validator, which enforces a strict 90-second tolerance window via `validateHmacTimestamp`. This mirrors the reported bug class: a security-relevant signal (a timestamp) exists but is not checked before the request is trusted and acted upon, letting a stale/replayed authenticated artifact be reused indefinitely.

### Finding Description
Two different HMAC validation code paths exist in the same file:

- Query-string based HMAC (used for OAuth `begin`/`callback` and app proxy requests) calls `validateHmacTimestamp()`, which requires the `timestamp` param to be within `HMAC_TIMESTAMP_PERMITTED_CLOCK_TOLERANCE_SEC` (90 seconds) of the current time before accepting the HMAC as valid: [1](#0-0) 

- Webhook body-based HMAC validation, `validateHmacFromRequestFactory`, only reads the `hmac` header and calls `validateHmacString()` to compare it against a locally computed HMAC of the raw body — there is no call to any timestamp/freshness check anywhere in this path: [2](#0-1) 

The webhook payload/headers do carry a `triggeredAt` field (`WEBHOOK_HEADER_NAMES[...].triggeredAt`), which is parsed and surfaced to the app as data, but it is never compared to the current time to reject old requests: [3](#0-2) [4](#0-3) 

The top-level `validateFactory` orchestrates only HMAC-body validation followed by a header presence check — no staleness/replay window is enforced: [5](#0-4) 

### Impact Explanation
Because the webhook signature check has no expiry/freshness enforcement (unlike the OAuth/app-proxy HMAC path, which explicitly bounds validity to 90 seconds), any previously-valid, HMAC-signed webhook request body+header combination remains "valid" forever. If such a request is ever captured (e.g., via logging, a proxy, browser history, or a compromised intermediary that only sees this one request), it can be replayed against the app's webhook endpoint at any point in the future and will be treated as an authentic, current Shopify event — triggering the app's webhook handler logic (e.g., `APP_UNINSTALLED`, order/customer data handlers) with stale/attacker-chosen timing. This is analogous to the oracle report's core defect: a freshness signal is available but not enforced, so authentication of "the request is current" silently degrades to authentication of "the request was once valid."

### Likelihood Explanation
Exploitation requires the attacker to have obtained one legitimately-signed webhook request body and headers at some point (this is the same precondition as any authenticated-request replay bug). This is weaker than a direct secret leak but does not require compromising the shared secret — only the ability to capture a single wire message. Given the library already goes out of its way to enforce a 90-second window on the structurally similar OAuth/app-proxy HMAC path, the absence of an equivalent, deliberate check on the webhook path is a real inconsistency, not a theoretical concern.

### Recommendation
Add a staleness check to `validateHmacFromRequestFactory`/`validateFactory` analogous to `validateHmacTimestamp`: read the `X-Shopify-Triggered-At` (webhooks) header (or `triggeredAt` for events webhooks) and reject the request if it falls outside an acceptable tolerance window (e.g., a few minutes) relative to the current time, in addition to the existing HMAC comparison.

### Proof of Concept
1. Capture one legitimate webhook POST (raw body + `X-Shopify-Hmac-Sha256` header) sent by Shopify to the app's webhook endpoint (e.g., via a debugging proxy, logs, or a single MITM opportunity).
2. At any later time, replay the exact same body and `hmac` header to the same endpoint.
3. `validateHmacFromRequestFactory` recomputes the HMAC over the (unchanged) raw body and compares it via `safeCompare`; since no timestamp check exists, `validHmac` is `true` and `validateFactory` returns `valid: true`, causing the app to process the replayed event as if it had just occurred.

### Citations

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L153-201)
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
}
```

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L203-232)
```typescript
function validateHmacTimestamp(query: AuthQuery) {
  const {timestamp} = query;

  if (
    timestamp === undefined ||
    timestamp === null ||
    Array.isArray(timestamp)
  ) {
    throw new ShopifyErrors.InvalidHmacError(
      'HMAC timestamp is missing or invalid',
    );
  }

  const parsedTimestamp = Number(timestamp);

  if (!Number.isInteger(parsedTimestamp)) {
    throw new ShopifyErrors.InvalidHmacError(
      'HMAC timestamp is missing or invalid',
    );
  }

  if (
    Math.abs(getCurrentTimeInSec() - parsedTimestamp) >
    HMAC_TIMESTAMP_PERMITTED_CLOCK_TOLERANCE_SEC
  ) {
    throw new ShopifyErrors.InvalidHmacError(
      'HMAC timestamp is outside of the tolerance range',
    );
  }
}
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

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L46-75)
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
}
```

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L139-146)
```typescript
  const triggeredAt = getHeader(headers, headerNames.triggeredAt);
  if (triggeredAt) fields.triggeredAt = triggeredAt;

  const eventId = getHeader(headers, headerNames.eventId);
  if (eventId) fields.eventId = eventId;

  return {valid: true, ...fields};
}
```
