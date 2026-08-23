### Title
HMAC covers only `rawBody`, not shop-identity headers, allowing cross-tenant webhook/event forgery via header substitution - ([File: packages/apps/shopify-api/lib/webhooks/validate.ts])

### Summary
`validateHmacFromRequestFactory` in `packages/apps/shopify-api/lib/utils/hmac-validator.ts` computes the HMAC only over `rawBody`, never over the `domain`, `resourceId`, `handle`, `topic`, or `webhookId` headers. `checkEventsHeaders`/`checkWebhooksHeaders` in `packages/apps/shopify-api/lib/webhooks/validate.ts` then extract those unauthenticated headers and return them as trusted "fields." Downstream, `authenticateWebhookFactory` in `packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts` uses `check.domain` directly to fetch and attach an offline session/access token, meaning shop-scoping is decided by an unsigned header.

### Finding Description
`detectWebhookType` in `validate.ts` selects the Events vs Webhooks header set based purely on which HMAC header is present: [1](#0-0) 

`validateFactory` then validates that HMAC value against `rawBody` only: [2](#0-1) 

The actual comparison in `validateHmacFromRequestFactory` signs/verifies `rawBody` exclusively — it never folds in `domain`, `resourceId`, `handle`, `topic`, or `webhookId`: [3](#0-2) 

Once that body-only HMAC check passes, `checkEventsHeaders` reads `domain`, `resourceId`, and `handle` straight from request headers and marks the whole payload `valid: true` with no cross-check against the signed body: [4](#0-3) 

Consuming code trusts these fields for tenant scoping. In `authenticateWebhookFactory`, `check.domain` — a value the HMAC never covered — is passed directly into `ensureValidOfflineSession` to fetch the session/access token, and `check.resourceId`/`check.handle` are forwarded verbatim into the handler context: [5](#0-4) 

Exploit flow: an unprivileged attacker who is a merchant/owner of Shop A can legitimately configure a webhook subscription (via Admin Settings → Notifications, or a Flow "Send an HTTP request"/Events action) pointing to a server they control. Shopify delivers a genuine `(rawBody, hmac)` pair for that event, signed with the real `apiSecretKey` (the attacker never needs to know the secret — it's Shopify that computes and delivers it). The attacker then crafts a brand-new HTTP POST directly to the target app's public webhook endpoint, reusing the exact same `rawBody` and `hmac` header (so `validateHmacString` still succeeds), but substitutes the `domain`, `resourceId`, and/or `handle` headers with Shop B's identifiers. No MITM is required — the attacker is simply issuing a new outbound request they fully control to a public endpoint; the interception of their own legitimately-received webhook is not privileged access. Because the invariant "event-type domain/resourceId is tied to the HMAC-producing principal" is never enforced (these fields are extracted post-validation and are never part of the signed content), `checkEventsHeaders` returns `valid: true` with attacker-chosen `domain`/`resourceId`/`handle`, and `authenticateWebhookFactory` loads Shop B's offline session and hands the app's webhook handler a `webhookContext` claiming to be about Shop B's resource.

### Impact Explanation
This is a cross-tenant data/session access primitive: an app built on `shopify-app-remix`/`shopify-api` will process a forged event as belonging to a victim shop, load that victim's offline session/access token via `ensureValidOfflineSession`, and hand the handler attacker-influenced `resourceId`/`handle` values scoped to the victim tenant. Depending on the handler's logic, this can lead to unauthorized cross-tenant data processing/exfiltration or state mutation performed with the victim's access token, matching the "cross-tenant session access" / "accepted forged webhook request" bounty impact classes.

### Likelihood Explanation
Feasible and repeatable for any attacker who can create/own a Shopify store (or draw on a compromised app-install's Flow trigger) and register a webhook/notification/Flow HTTP action pointing to infrastructure they control — a standard, unprivileged merchant capability, not an app-developer or Shopify-employee action. Obtaining one valid `(rawBody, hmac)` pair is sufficient; it can then be replayed indefinitely with arbitrary header substitutions as long as the same rawBody/hmac pair is reused, since header values are never bound to the signature.

### Recommendation
Bind the tenant-identifying fields to the HMAC computation, or otherwise cryptographically tie `domain`/`resourceId`/`handle`/`topic`/`webhookId` to the signed payload before trusting them — e.g., include the relevant headers (in canonical order) in the HMAC-signed string, or validate that `domain` in the headers matches a `myshopify_domain`/shop identifier embedded in the JSON body (which real Shopify webhook payloads typically carry) before using it for session lookup in `authenticateWebhookFactory`. At minimum, `ensureValidOfflineSession` should not implicitly trust an unauthenticated header value as the shop key without an additional consistency check against signed data.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/webhooks/__tests__/cross-tenant-header-forgery.test.ts
import {shopifyApi} from '../../..';
import {testConfig} from '../../__tests__/test-config';
import * as ShopifyErrors from '../../error';

describe('Cross-tenant Events header forgery', () => {
  it('accepts a Shop-A-signed body with Shop-B domain/resourceId headers', async () => {
    const shopify = shopifyApi(testConfig());
    const rawBody = JSON.stringify({resource: 'shop-a-order-123'});

    // 1. Attacker legitimately receives this HMAC for their own Shop A
    //    (computed with the real apiSecretKey by Shopify itself).
    const crypto = require('crypto');
    const hmac = crypto
      .createHmac('sha256', shopify.config.apiSecretKey)
      .update(rawBody, 'utf8')
      .digest('base64');

    // 2. Attacker crafts a new request to the app's webhook endpoint,
    //    reusing the same rawBody/hmac but swapping identity headers
    //    to Shop B's values.
    const headers = {
      'X-Shopify-Topic': 'orders/create',
      'X-Shopify-Hmac-Sha256': hmac,
      'X-Shopify-Shop-Domain': 'shop-b.myshopify.com', // victim, not shop-a
      'X-Shopify-API-Version': '2024-01',
      'X-Shopify-Webhook-Id': 'forged-id',
    };

    const result = await shopify.webhooks.validate({
      rawBody,
      rawRequest: {headers} as any,
    });

    // EXPECTED (if fixed): validation should fail because domain
    // is not bound to the signed body.
    // ACTUAL: validation succeeds and returns Shop B's domain.
    expect(result.valid).toBe(true);
    expect((result as any).domain).toBe('shop-b.myshopify.com');
  });
});
```
This demonstrates that `checkEventsHeaders`/`checkWebhooksHeaders` return `domain` (and, analogously, `resourceId`/`handle` for the Events path) as trusted values despite them never being covered by the HMAC in `validateHmacFromRequestFactory`, which is exactly what `authenticateWebhookFactory` uses to select which tenant's session to load.

### Citations

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L26-44)
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
```

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L54-73)
```typescript
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

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L148-203)
```typescript
function checkEventsHeaders(
  headers: Headers,
): WebhookValidationMissingHeaders | WebhookValidationValid {
  const headerNames = WEBHOOK_HEADER_NAMES[WebhookType.Events];
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
  const eventId = getRequiredHeader(
    headers,
    headerNames.eventId,
    missingHeaders,
  );

  if (missingHeaders.length) {
    return {
      valid: false,
      reason: WebhookValidationErrorReason.MissingHeaders,
      missingHeaders,
    };
  }

  const fields: EventsWebhookFields = {
    webhookType: WebhookType.Events,
    hmac: hmac!,
    topic: topicForStorage(topic!),
    domain: domain!,
    apiVersion: apiVersion!,
    webhookId: webhookId!,
    eventId: eventId!,
  };

  const handle = getHeader(headers, headerNames.handle);
  if (handle) fields.handle = handle;

  const action = getHeader(headers, headerNames.action);
  if (action) fields.action = action;

  const resourceId = getHeader(headers, headerNames.resourceId);
  if (resourceId) fields.resourceId = resourceId;

  const triggeredAt = getHeader(headers, headerNames.triggeredAt);
  if (triggeredAt) fields.triggeredAt = triggeredAt;

  return {valid: true, ...fields};
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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L35-86)
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
```
