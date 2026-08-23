Confirmed: `validateHmacFromRequestFactory` in [1](#0-0)  computes the webhook HMAC purely over `rawBody` using the app's single, shop-independent `apiSecretKey`, and the `X-Shopify-Shop-Domain` header (`domain`) is read out separately and unauthenticated in `checkWebhooksHeaders`/`checkEventsHeaders` in [2](#0-1) . This is the same root-cause shape as the report: one value (`domain`) that downstream code trusts for authorization/session-lookup is never bound to the value that was actually cryptographically verified (`rawBody`+shared secret). Because the API secret is identical for every shop that installs the app, any shop that receives a legitimately-signed webhook can capture `rawBody`+`X-Shopify-Hmac-Sha256` and replay it to the app's webhook endpoint with a different `X-Shopify-Shop-Domain` (and other headers), producing a request that still passes `validateHmacFromRequestFactory` but is then processed under an attacker-chosen shop identity via `check.domain` in `authenticateWebhookFactory` (`packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts` and the react-router equivalent), which is used to load the session (`ensureValidOfflineSession(params, check.domain)`) and construct the webhook context.

I'll verify this is exploitable end-to-end (i.e., that nothing else binds domain to the signed payload, and that `ensureValidOfflineSession`/session storage will happily return a session for any shop string) before finalizing.### Title
Webhook shop identity (`X-Shopify-Shop-Domain`) is not bound to the HMAC-signed payload, allowing cross-tenant webhook forgery via replay - (File: `packages/apps/shopify-api/lib/utils/hmac-validator.ts`, `packages/apps/shopify-api/lib/webhooks/validate.ts`)

### Summary
Webhook authenticity is verified by `validateHmacFromRequestFactory` using only `rawBody` and the app's single, shop-independent `apiSecretKey`. The `X-Shopify-Shop-Domain` header, which downstream code trusts as "the shop this webhook is for," is read separately and is never included in the HMAC computation. Because the same `apiSecretKey` is used to sign webhooks for every shop that has installed the app, a value that is cryptographically authenticated (the body/HMAC pair) is decoupled from the value that authorization/session-lookup logic actually relies on (the domain header) — the same class of bug as the report: two related values that should be tied together (amount signed vs. amount used) are instead independently sourced, and only one is validated.

### Finding Description
`validateHmacFromRequestFactory` (`packages/apps/shopify-api/lib/utils/hmac-validator.ts`, lines 168-200) computes `validHmac` from `rawBody` alone: [3](#0-2) 

`validateFactory` in `packages/apps/shopify-api/lib/webhooks/validate.ts` then, only after this body-only HMAC check passes, extracts the shop identity from the unauthenticated `X-Shopify-Shop-Domain` header via `checkWebhooksHeaders`/`checkEventsHeaders`: [4](#0-3) 

This `domain` field is then propagated as `check.domain` and used directly to look up/create the offline session and build the webhook execution context, e.g. in `authenticateWebhookFactory`: [5](#0-4) 

Since `apiSecretKey` is per-app (not per-shop), any tenant that has legitimately installed the app receives real webhooks with a valid `rawBody` + `X-Shopify-Hmac-Sha256` pair signed with that same shared secret. That HMAC value only proves "Shopify (or someone knowing the app secret) produced this body" — it says nothing about which shop it was for. An attacker who controls one installed shop can capture a legitimate `(rawBody, hmac)` pair from their own webhook deliveries and resend the exact same body/HMAC to the app's webhook endpoint while substituting a different `X-Shopify-Shop-Domain` header (and other unauthenticated headers such as topic/webhook ID) corresponding to a victim shop that also installed the app. The request still passes HMAC validation (body unchanged) and `checkWebhookHeaders` (headers present), and the app then processes it as a legitimate webhook "from" the victim shop, loading that shop's offline session and invoking the handler/callback with attacker-controlled event content attributed to the victim.

### Impact Explanation
This lets an unprivileged actor (any merchant who installs the app) forge webhook events that the app believes originate from a different tenant. Depending on what the app's webhook handlers do with `shop`/`topic`/payload (common actions include updating billing state, product/order data, deleting resources, or triggering business logic keyed off `shop_domain`), this is a cross-tenant confused-deputy vulnerability: it can corrupt another merchant's data, trigger unwanted side effects using the victim's offline access token, or desynchronize app state for a shop the attacker does not control. It does not directly leak the victim's access token, but it lets the attacker exercise the app's server-side logic (including outbound Admin API calls made using the victim's session) under a spoofed shop identity.

### Likelihood Explanation
Likelihood is high for any app that relies on `shopify.webhooks.process`/`validate` (or the `shopify-app-remix`/`shopify-app-react-router` `authenticate.webhooks`) with more than one installed shop, since:
- No special knowledge beyond installing the app and observing one's own webhook traffic (`rawBody` + `X-Shopify-Hmac-Sha256` header) is required.
- The only "attack" needed is a normal HTTP replay with a modified `X-Shopify-Shop-Domain` header — no cryptography needs to be broken.
- This works for any webhook topic whose payload the attacker's own shop can trigger with content useful against a victim (e.g., generic app-specific topics with attacker-influenced content), and is not mitigated by the 90s timestamp check used for OAuth/app-proxy HMACs (webhook HMAC validation has no timestamp/nonce/replay window at all).

### Recommendation
Bind the shop identity to the authenticated payload rather than trusting the header value independently:
- Require that `domain` (and ideally `topic`/`webhook_id`) be embedded in, or otherwise cryptographically tied to, the signed payload, or use a per-shop-derived secret when validating headers.
- At minimum, enforce webhook idempotency/replay protection keyed on `webhookId`/`eventId` (already present in headers) so a captured body cannot be resent, and cross-check that the session found for `check.domain` was actually the one that installed/authorized that specific webhook subscription.
- Consider verifying, where possible, that the resolved `domain` corresponds to a shop that currently has an active, matching webhook subscription for that specific `webhookId`, rather than accepting any domain header value once the (shop-independent) body HMAC checks out.

### Proof of Concept
1. App is installed by both Shop A (attacker-controlled) and Shop B (victim), using the same app (same `apiSecretKey`).
2. Shopify delivers a legitimate webhook to the app's endpoint for Shop A: body `B`, headers including `X-Shopify-Hmac-Sha256: H` (valid for `B`) and `X-Shopify-Shop-Domain: shop-a.myshopify.com`.
3. Attacker (operator of Shop A) captures `(B, H)` from their own server logs.
4. Attacker sends a new POST to the app's webhook endpoint with the same body `B` and same `X-Shopify-Hmac-Sha256: H`, but sets `X-Shopify-Shop-Domain: shop-b.myshopify.com` (and, if relevant, a different `X-Shopify-Topic`/`X-Shopify-Webhook-Id`, which are also unauthenticated).
5. `validateHmacFromRequestFactory` recomputes the HMAC from body `B` only — it matches `H`, so `validHmac` is `true` regardless of the domain header, per [6](#0-5) .
6. `checkWebhooksHeaders` extracts `domain: 'shop-b.myshopify.com'` from the header, per [7](#0-6) , and the app processes the webhook as if it came from Shop B, loading Shop B's offline session in `ensureValidOfflineSession(params, check.domain)`.

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L40-76)
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
```
