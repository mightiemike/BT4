This confirms the vulnerability path. The HMAC in `validateHmacFromRequestFactory` (`packages/apps/shopify-api/lib/utils/hmac-validator.ts`) is computed over `rawBody` alone, and the `domain`/`topic`/`webhookId` values used to route the webhook to shop-specific handlers in `callWebhookHandlers` (`packages/apps/shopify-api/lib/webhooks/process.ts`) come from unauthenticated headers extracted in `checkWebhookHeaders` (`packages/apps/shopify-api/lib/webhooks/validate.ts`).

### Title
Webhook HMAC does not bind the `X-Shopify-Shop-Domain`/topic headers, allowing cross-tenant webhook replay - (File: packages/apps/shopify-api/lib/utils/hmac-validator.ts)

### Summary
The shopify-app-js webhook authentication flow computes the HMAC signature over the raw request body only, never over the `X-Shopify-Shop-Domain`, `X-Shopify-Topic`, or `X-Shopify-Webhook-Id` headers. These headers, however, are what the library and app code use to decide *which shop's offline session* to load and which webhook handler receives the payload. Because the header values are not part of the signed digest, they can be swapped in-flight without invalidating the signature—directly analogous to the SmartSession bug where `permissionID` is decoded/used but never included in the digest that legitimizes the call.

### Finding Description
`validateHmacFromRequestFactory` reads the `hmac` header and validates it strictly against `rawBody`: [1](#0-0) 

The webhook headers (`domain`, `topic`, `webhookId`, `apiVersion`) are extracted **after** this HMAC check succeeds, and are never fed into the HMAC computation: [2](#0-1) 

`checkWebhooksHeaders`/`checkEventsHeaders` simply pull these fields straight from request headers with no cryptographic binding to the HMAC-verified body: [3](#0-2) 

Downstream, `domain` (the unauthenticated header) is used directly to route the webhook and select which shop-specific handler and shop context is invoked: [4](#0-3) 

And in the app-framework packages (Remix/React-Router), the same unauthenticated `check.domain` is used to load the *offline session* for that shop and hand an authenticated `admin` client back to app code: [5](#0-4) 

Because the entire app shares a single `apiSecretKey` across all installed shops (this is not a per-shop secret), any single merchant that has the app installed on their own store can capture a legitimately-HMAC'd webhook body that Shopify sent for a real event on their store, then replay that exact HTTP request to the app's public webhook endpoint while substituting the `X-Shopify-Shop-Domain` (and/or `X-Shopify-Topic`/`X-Shopify-Webhook-Id`) header to point at a **different, victim shop**. The HMAC only signs the body/secret pair, so it will still validate; the header substitution goes completely unnoticed since it is never part of the digest — mirroring exactly how `permissionID` in SmartSession is parsed and used to route policy application but is never part of the digest that legitimizes the call.

### Impact Explanation
If successfully replayed, the app will:
- Load the victim shop's offline session (`ensureValidOfflineSession(params, check.domain)`), handing app webhook-handler code an authenticated `admin` API client scoped to the victim's shop.
- Process attacker-controlled body content (from the attacker's own legitimately-signed webhook) as if it originated from the victim shop and topic.

This is a cross-tenant confused-deputy: attacker-controlled data is processed under a different tenant's authenticated session, which could trigger data writes, GDPR-topic processing, or other webhook-handler side effects against the wrong shop, and/or cause the app to record/act on forged shop-attribution data.

### Likelihood Explanation
Any merchant/customer who can install the app (or otherwise receive at least one legitimately signed webhook from Shopify for their own store) can mount this attack purely by replaying a captured HTTP request with a modified header value against the app's public `/webhooks` endpoint — no MITM, no secret leakage, and no elevated privilege is required. The webhook endpoint is by design a public, unauthenticated HTTP endpoint that must accept requests from the open internet.

### Recommendation
Bind the shop-identifying/topic-identifying headers into the HMAC digest (e.g., include `X-Shopify-Shop-Domain`, `X-Shopify-Topic`, and `X-Shopify-Webhook-Id` as part of the signed payload, or require the caller to independently corroborate the header values against Shopify's known/expected shop registry state) rather than trusting header values that were never covered by `validateHmacString`. At minimum, cross-check the `domain` header against the shop actually associated with the given `webhookId`/subscription before loading a session for it, similar to the fix recommended for SmartSession: add the routing identifier into the verified digest computation rather than trusting it out-of-band.

### Proof of Concept
1. Install the target app on attacker-controlled store `attacker.myshopify.com`, or otherwise obtain a legitimate webhook delivery from Shopify (e.g., trigger `PRODUCTS_CREATE`) — capture the full raw POST request including body and the `X-Shopify-Hmac-Sha256`, `X-Shopify-Shop-Domain`, `X-Shopify-Topic`, `X-Shopify-Webhook-Id` headers.
2. Replay the exact same request to the app's public `/webhooks` endpoint, keeping `rawBody` and `X-Shopify-Hmac-Sha256` unchanged, but set `X-Shopify-Shop-Domain: victim.myshopify.com`.
3. Observe that `validateHmacFromRequestFactory` succeeds (HMAC only covers `rawBody`), `checkWebhooksHeaders` returns `domain: 'victim.myshopify.com'`, and the app loads/uses the victim shop's offline session/admin client while processing the attacker's payload — confirming cross-tenant webhook injection.

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

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L99-134)
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

**File:** packages/apps/shopify-api/lib/webhooks/process.ts (L146-156)
```typescript
    const {webhookId} = webhookCheck;

    try {
      await handler.callback(
        webhookCheck.topic,
        webhookCheck.domain,
        rawBody,
        webhookId,
        webhookCheck.apiVersion,
        context,
      );
```

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/webhooks/authenticate.ts (L52-96)
```typescript
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
```
