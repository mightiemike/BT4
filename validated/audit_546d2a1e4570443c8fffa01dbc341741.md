### Title
Webhook shop-domain/topic/webhookId headers are unauthenticated and not bound to the HMAC-signed body, enabling cross-tenant webhook replay - ([File: packages/apps/shopify-api/lib/webhooks/validate.ts])

### Summary
`shopify.webhooks.validate` (and the `authenticate.webhook` wrappers built on it) only verifies the `X-Shopify-Hmac-Sha256` value against the raw request body. The `X-Shopify-Shop-Domain`, `X-Shopify-Topic`, `X-Shopify-Webhook-Id`, and `X-Shopify-API-Version` headers are read directly off the unauthenticated request and are never included in the HMAC computation. This is the same bug class as the DKIM report: a "valid signature" check is performed on only part of the payload (`rawBody`), while a separate field that drives security-relevant behavior (`domain`, used to select which shop's offline session/access token gets attached to the webhook context) is left completely unvalidated, so a previously-valid (body, hmac) pair can be replayed with a different target identifier.

### Finding Description
`validateHmacFromRequestFactory` computes the local HMAC solely from `rawBody` and the app's shared `apiSecretKey`: [1](#0-0) 

`validateFactory` in `webhooks/validate.ts` calls this HMAC check and, only if it passes, extracts `domain`, `topic`, `webhookId`, `apiVersion` from headers with no cryptographic linkage to the verified body: [2](#0-1) [3](#0-2) 

The `WEBHOOK_HEADER_NAMES` mapping shows these fields (`hmac`, `topic`, `domain`, `apiVersion`, `webhookId`) are all separate HTTP headers, and only `hmac` participates in the signature: [4](#0-3) 

Downstream, the `domain` value returned from this unauthenticated check is used directly to select and load the *target shop's* offline session and build an authenticated admin client: [5](#0-4) 

Since Shopify's real webhook HMAC is computed over the raw body using the app's single, shared `apiSecretKey` (not tied to any shop, topic, or webhook id), any request whose body is byte-identical to a body Shopify once genuinely signed for the app will still pass this check — regardless of which shop's domain header is attached. A merchant who installs the app (an ordinary, unprivileged actor from the library's perspective) legitimately receives real webhooks (e.g. `app/uninstalled`) with valid HMACs for their own shop's body. That exact `(rawBody, hmac)` pair can be resent to the app's webhook endpoint with the `X-Shopify-Shop-Domain` header changed to a different (victim) shop that also installed the app. `validateFactory` will report `valid: true` because only the body/HMAC pair is checked, and `check.domain` will be the attacker-chosen victim domain, causing `ensureValidOfflineSession`/webhook handler dispatch to run against the victim shop's stored session and access token instead of the attacker's own shop.

This is a direct structural analog to the reported bug: the "identifier" used to decide which target the operation applies to (`domain`, analogous to the DKIM `identifier`/`sigMeta`) is derived from data outside the validated set, letting an attacker keep the valid proof-of-authenticity (HMAC) constant while freely varying the field that determines *whose* state is affected.

### Impact Explanation
An attacker (any merchant who has installed the app) can:
- Trigger webhook processing/handlers to run in the security context of a different shop (cross-tenant confusion), since `session`/`admin` in the webhook context are derived from the forged `domain`.
- Replay a genuine `app/uninstalled`-type webhook against a victim shop's domain, which (in `shopify-app-express`'s `AppInstallations.delete`, wired to uninstall handling) deletes all stored sessions/access tokens for that shop [6](#0-5) , causing denial of service / loss of app access for a shop that never actually uninstalled — directly mirroring the "loss of access" impact in the original report.
- More generally, cause any webhook handler that trusts `shop`/`session` to execute with mismatched shop context and attacker-controlled (though previously-Shopify-signed) payload content.

### Likelihood Explanation
Medium: exploitation only requires an actor who has installed the app on at least one shop (to legitimately obtain a valid signed webhook), the app to be installed by a second (victim) shop, and the attacker to resend a captured HTTP request with one header changed — no cryptographic secret needs to be recovered. This matches the "specific conditions but plausible" likelihood profile of the original finding.

### Recommendation
Do not trust `X-Shopify-Shop-Domain` (or `topic`/`webhookId`/`apiVersion`) purely from headers when they are not covered by the HMAC. At minimum:
- Cross-check the `domain` header against an expected/allow-listed shop (e.g., compare to a shop the app already has an active session for, or validate it structurally and confirm consistency across repeated deliveries via `webhookId` uniqueness tracking).
- Where possible, include `domain`, `topic`, and `webhookId` in an application-level replay/duplicate cache keyed by `webhookId` (Shopify does send a unique `X-Shopify-Webhook-Id` per delivery) to reject repeated bodies, and separately verify that the `domain` in the header actually owns the session being mutated, rather than blindly trusting it post-HMAC-check.

### Proof of Concept
1. Attacker installs the app on `attacker.myshopify.com`; the app registers webhook handlers.
2. Attacker triggers (or waits for) a genuine webhook, e.g. `app/uninstalled`, and captures the raw HTTP request Shopify sent, including body and the `X-Shopify-Hmac-Sha256` header — this HMAC is valid because it's computed only from `rawBody` and the app's shared secret: [7](#0-6) 
3. Attacker resends the identical body and `X-Shopify-Hmac-Sha256` value to the app's `/webhooks` endpoint, but sets `X-Shopify-Shop-Domain: victim.myshopify.com` (a shop that has installed the app).
4. `shopify.webhooks.validate` returns `valid: true` with `domain: 'victim.myshopify.com'` because the HMAC check only inspects `rawBody`: [8](#0-7) 
5. `authenticateWebhookFactory` calls `ensureValidOfflineSession(params, 'victim.myshopify.com')`, loading the victim's session/access token and dispatching the handler as though the event genuinely originated from the victim shop: [9](#0-8) 
6. If the replayed topic is `app/uninstalled`, this results in deletion of the victim shop's stored sessions via `AppInstallations.delete`, denying the victim access to the app despite never uninstalling it.

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L33-102)
```typescript
    const rawBody = await request.text();

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

**File:** packages/apps/shopify-app-express/src/app-installations.ts (L33-41)
```typescript
  async delete(shopDomain: string): Promise<void> {
    const shopSessions =
      await this.sessionStorage.findSessionsByShop!(shopDomain);
    if (shopSessions.length > 0) {
      await this.sessionStorage.deleteSessions!(
        shopSessions.map((session: Session) => session.id),
      );
    }
  }
```
