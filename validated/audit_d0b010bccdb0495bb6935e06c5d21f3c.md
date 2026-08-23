## Vulnerability Analog Found

### Title
Webhook `domain` header is not covered by HMAC signature verification, enabling cross-tenant session/admin-API access via header spoofing - (File: `packages/apps/shopify-api/lib/webhooks/validate.ts`)

### Summary
Shopify webhook authenticity is verified purely by comparing an HMAC computed over the raw request body against the `X-Shopify-Hmac-Sha256` header. The `X-Shopify-Shop-Domain` (and equivalent Events header) that identifies *which* shop the webhook belongs to is never included in that HMAC computation, yet it is trusted at face value to look up the shop's offline session and construct an authenticated Admin API client that is handed to the app's webhook handler.

### Finding Description
`validateHmacFromRequestFactory` computes HMAC over `rawBody` only, comparing it with the value of the `hmac` header: [1](#0-0) 

The shop-identifying `domain` header is then pulled straight out of request headers with no signature binding to the body/HMAC pair, and is returned as a "valid" field once required headers are present: [2](#0-1) 

Because the HMAC never covers the `domain` header, any string value in `X-Shopify-Shop-Domain` will pass validation as long as the accompanying body+hmac pair is a value that was ever legitimately produced for *some* shop’s webhook (including the attacker's own shop, which they fully control and receive webhooks for).

Consumers of `shopify.webhooks.validate()` (e.g. `authenticateWebhookFactory` in shopify-app-remix / shopify-app-react-router) take the unauthenticated `check.domain` value and use it directly to load the offline session and build an authenticated Admin API client: [3](#0-2) 

`ensureValidOfflineSession(params, check.domain)` resolves and returns whatever shop's stored offline session matches `check.domain`, and `adminClientFactory` then wires that session's `accessToken` into a live Admin GraphQL client that is exposed as `admin` in the webhook context passed to the app's handler code.

### Impact Explanation
An attacker who has installed the app on their own shop (or who is a normal merchant/customer able to trigger any webhook the app subscribes to, e.g. `orders/create`) receives at least one legitimate `(rawBody, hmac)` pair for their own shop. Because the HMAC signature never binds to the shop domain, the attacker can replay that exact body+hmac to the app's webhook endpoint while substituting an arbitrary `X-Shopify-Shop-Domain` value naming a *different, victim* shop that also has the app installed. `shopify.webhooks.validate()` reports `valid: true` with `domain` equal to the attacker-chosen victim shop. The webhook handler then receives `webhookContext.shop` set to the victim shop and an `admin` client authenticated with the victim shop's real offline access token — a cross-tenant authorization bypass that lets one merchant/customer trigger app business logic (and any Admin API calls the handler makes) against another shop's store data/access token.

### Likelihood Explanation
Reachable by an unprivileged, anonymous-to-the-victim actor: the attacker only needs their own (or any other) installed shop to obtain one valid `(body, hmac)` sample for a webhook topic the app registers, then POST it to the public webhook endpoint with a forged `Shop-Domain` header. No secret material, MITM, or privileged access to the victim's environment is required — this is a pure header-trust flaw in the validation function that every app built on `shopify.webhooks.validate()`/`authenticateWebhookFactory` inherits.

### Recommendation
- Do not treat the `domain` (or `shop`) header as trusted identification on its own; where feasible, incorporate the shop domain into the HMAC computation, or cross-validate it against another signed value (e.g., verify the webhook `webhookId` is unique-per-delivery and track/dedupe per shop, or require the caller to independently confirm the shop identity, such as checking that the domain matches an expected value passed by the caller of `validate()`).
- At minimum, sanitize/validate the `domain` header using `sanitizeShop` before returning it as trusted output, and document/enforce that consumers must not use the `domain` field to authorize cross-tenant admin operations without additional binding to the signed payload.
- Consider adding replay protection (e.g., tracking `webhookId`/timestamp per shop) so a captured valid pair cannot be reused with a different domain claim.

### Proof of Concept
1. Install the app on `attacker-shop.myshopify.com`; trigger a webhook (e.g. `orders/create`) and capture the raw POST: body `B` and header `X-Shopify-Hmac-Sha256: H`.
2. Also install (or have installed) the same app on `victim-shop.myshopify.com`.
3. Send a new POST to the app's webhook endpoint with: `body = B`, `X-Shopify-Hmac-Sha256 = H` (unchanged, still valid because it's computed only from `B`), but `X-Shopify-Shop-Domain: victim-shop.myshopify.com` and other required headers filled arbitrarily.
4. `shopify.webhooks.validate()` returns `{valid: true, domain: 'victim-shop.myshopify.com', ...}` per `checkWebhooksHeaders`.
5. `authenticateWebhookFactory` calls `ensureValidOfflineSession(params, 'victim-shop.myshopify.com')`, retrieves the victim's stored offline session/access token, and constructs `admin` from it — the app's webhook handler now executes with the victim shop's identity/credentials in response to attacker-supplied payload `B`.

### Citations

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L189-197)
```typescript
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

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/webhooks/authenticate.ts (L35-96)
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
```
