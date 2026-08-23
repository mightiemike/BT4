This confirms the domain header is a plain, unsigned header — the HMAC only covers `rawBody` and shares a single app-wide secret across all installed shops, with no binding of `X-Shopify-Shop-Domain` to the signature.### Title
Webhook `X-Shopify-Shop-Domain` header is not bound to the HMAC signature, allowing cross-tenant impersonation via replayed webhook payloads - (File: `packages/apps/shopify-api/lib/webhooks/validate.ts`)

### Summary
The bug class in the reference report is a missing binding/ownership check between a caller-supplied identifier (`tokenId`) and the entity actually authorized by the signed/authenticated context (`_posId`). The analog in `shopify-app-js` is `shopify.webhooks.validate()`: it authenticates the request body via HMAC but never binds the `X-Shopify-Shop-Domain` header (the tenant identifier) to that signature, so any body/HMAC pair a merchant has legitimately received can be replayed with a different shop-domain header and will still validate as "coming from" that other shop.

### Finding Description
`validateFactory` in [1](#0-0)  validates a webhook purely by:
1. Computing `HMAC-SHA256(apiSecretKey, rawBody)` and comparing it to the `X-Shopify-Hmac-Sha256` header, via `validateHmacFromRequestFactory` in [2](#0-1) .
2. Then separately calling `checkWebhookHeaders`, which merely checks that headers like `domain`, `topic`, `webhookId` are *present* (not that they cryptographically match anything) — see `checkWebhooksHeaders` in [3](#0-2) .

Critically, the `X-Shopify-Shop-Domain` header (`WEBHOOK_HEADER_NAMES[...].domain`) declared in [4](#0-3)  is **not included in the HMAC input** — only `rawBody` is hashed. The `apiSecretKey` used for signing is shared across every shop that has installed the app (it is the app's client secret, not a per-shop secret). This means the domain header is an unauthenticated, attacker-controllable field: whoever can present *any* valid `(rawBody, hmac)` pair — trivially available to any merchant who has legitimately installed the app and received one real webhook — can resend that exact body/HMAC to the app's webhook endpoint with the `X-Shopify-Shop-Domain` header changed to a *different* (victim) shop's domain, and `validate()` will report `valid: true, domain: <victim-shop>`.

Downstream, `authenticateWebhookFactory` (used by both the Remix and React Router adapters) trusts `check.domain` unconditionally to look up and attach the victim's session/`admin` client: [5](#0-4) 
The `session = await ensureValidOfflineSession(params, check.domain)` call at line 52 loads the **victim shop's own offline access token** and hands it to the app's webhook callback (`webhookContext.admin`), as if the request genuinely originated from the victim.

This is structurally identical to the reference bug: a caller-controlled identifier (`_tokenId` / here, the `domain` header) is used to select another tenant's resource (`posCollInfo` / here, the victim's `Session` and access token) without verifying that the identifier is actually bound to the authenticated proof (position ownership / here, the HMAC signature).

### Impact Explanation
An attacker who is any merchant using the app (a low-privilege, single-merchant actor — no leaked secrets, no MITM required) can:
- Capture the raw body + valid `X-Shopify-Hmac-Sha256` of any webhook legitimately delivered to their own shop.
- Replay it to the app's webhook endpoint with `X-Shopify-Shop-Domain` set to any other shop that uses the app.
- Cause the app to treat the request as authenticated for that victim shop, loading the victim's offline session and passing the victim's `admin` GraphQL/REST client into the developer's webhook handler together with attacker-controlled payload content (their own webhook body).

Depending on how the app's webhook handlers use `admin`/`payload`/`shop`, this enables cross-tenant confused-deputy actions using the victim's access token (e.g. mutating/deleting victim data keyed by attacker-supplied IDs, triggering app-uninstall cleanup against the wrong shop, corrupting billing/webhook-driven state, etc.). This matches the "cross-tenant access" / "accepted forged Shopify request" acceptance criteria.

### Likelihood Explanation
Likelihood is high for any app that relies solely on `shopify.webhooks.validate()` / the `authenticate.webhook` helpers to authorize webhook-driven side effects, because:
- No secret compromise or MITM is required — replaying your own legitimately signed payload is trivially available to any app user.
- Shop domains (`*.myshopify.com`) are frequently guessable/enumerable/public.
- The library itself provides no built-in cross-check between the signed body and the domain header, and the documentation/example usage (`shopify.webhooks.validate` guide) does not warn developers to independently verify shop/domain consistency.

### Recommendation
Bind the shop domain (and other headers used for authorization decisions, e.g. `topic`, `webhookId`) into the HMAC computation, or otherwise cryptographically attest that the domain header belongs to the same signed request — for example by including the `X-Shopify-Shop-Domain` value in the string that is hashed/compared in `validateHmacFromRequestFactory` (`packages/apps/shopify-api/lib/utils/hmac-validator.ts`), or by having `validateFactory`/`checkWebhookHeaders` reject a request unless the domain is independently confirmed (e.g., cross-checked against a per-shop webhook secret if Shopify supports one, or against a nonce/idempotency mechanism keyed to `webhookId`+`domain` to detect replays across tenants). At minimum, document prominently that `check.domain` must not be trusted for authorization purposes without additional validation, and encourage per-webhook idempotency and shop verification in the `authenticate.webhook` helpers before attaching a session/`admin` client.

### Proof of Concept
1. Install the target app on `attacker-shop.myshopify.com` and let it trigger any subscribed webhook (e.g., `ORDERS_CREATE`), capturing the raw POST body `B` and the `X-Shopify-Hmac-Sha256: H` header Shopify sent.
2. Send a new POST to the app's webhook endpoint with:
   - Body: `B` (unchanged)
   - `X-Shopify-Hmac-Sha256: H` (unchanged, still validates because HMAC only covers the body and the shared `apiSecretKey`)
   - `X-Shopify-Shop-Domain: victim-shop.myshopify.com` (changed)
   - `X-Shopify-Topic`, `X-Shopify-Webhook-Id`, `X-Shopify-API-Version` set to arbitrary/valid-looking values.
3. `shopify.webhooks.validate()` (`packages/apps/shopify-api/lib/webhooks/validate.ts`) returns `{valid: true, domain: 'victim-shop.myshopify.com', ...}` because the HMAC check only verifies `B` against `H`, and `checkWebhookHeaders` only checks header *presence*.
4. In `authenticateWebhookFactory` (`packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts`, lines 35-96), `ensureValidOfflineSession(params, check.domain)` loads `victim-shop.myshopify.com`'s real offline session/access token and passes it, plus the attacker's own webhook payload, into the app's webhook handler as `{shop: 'victim-shop.myshopify.com', payload: <attacker body>, admin: <victim's admin client>}`.

### Citations

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L35-102)
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
