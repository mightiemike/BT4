This confirms the vulnerability chain. Webhook HMAC validation in `validateHmacFromRequestFactory` (`packages/apps/shopify-api/lib/utils/hmac-validator.ts:168-201`) computes the signature over `rawBody` only — the `X-Shopify-Shop-Domain`, `X-Shopify-Topic`, and `X-Shopify-Webhook-Id` headers are never part of the signed data.### Title
Webhook shop-domain header is not covered by the HMAC signature, allowing a forged webhook to impersonate any installed shop and obtain that shop's offline session/Admin API client - ([File: packages/apps/shopify-api/lib/webhooks/validate.ts])

### Summary
The webhook authentication pipeline validates a webhook request's authenticity by checking only the HMAC over the raw request body against the app's shared secret. The `X-Shopify-Shop-Domain` header (and `X-Shopify-Topic`/`X-Shopify-Webhook-Id`) are read directly off the unauthenticated headers and are never included in the signed payload. `authenticateWebhookFactory` then uses this unauthenticated `domain` value to look up/mint the shop's offline session and construct an authenticated Admin API client for the webhook handler, without any check that the domain is actually tied to the signed body.

### Finding Description
`validateHmacFromRequestFactory` computes and checks the webhook signature using only `rawBody`: [1](#0-0) 

`validateFactory` in `validate.ts` calls this HMAC check, and then — completely independently — reads `domain`, `topic`, and `webhookId` straight from request headers with no cross-binding to the HMAC-covered body: [2](#0-1) 

`authenticateWebhookFactory` (shared pattern in both shopify-app-remix and shopify-app-react-router) trusts `check.domain` (the unauthenticated header value) as the shop identity, and uses it to load/refresh that shop's offline session and build an Admin API client: [3](#0-2) 

Because the HMAC secret (`apiSecretKey`) is shared across every shop that installs a given app (it is per-app, not per-shop), any merchant who installs the same public app can obtain a body+HMAC pair that Shopify computed as valid for their own shop (e.g., by registering a webhook subscription to an endpoint they control and capturing the delivered request). Since the `domain` header is not part of the signed data, that same body+HMAC pair can be replayed directly to the app's public webhook endpoint with the `X-Shopify-Shop-Domain` header changed to any other shop that has the app installed. The signature check still passes (it never inspected the header), `checkWebhookHeaders` accepts the forged `domain`, and `ensureValidOfflineSession`/`loadOfflineSession` then hands the webhook handler a legitimate offline session and Admin API client for the *victim* shop — even though the payload content and triggering event actually originated from the attacker's own shop.

This is structurally the same bug class as the MechMarketplace `create()` flaw: an endpoint accepts a caller-influenced identifier (`serviceId` there, shop `domain` here) without verifying that the identifier is cryptographically bound to the authenticated portion of the request, letting an unprivileged caller impersonate another tenant.

### Impact Explanation
An attacker who is merely a merchant/customer of the same public app (an "unprivileged" party relative to the victim shop) can:
- Trigger the app's webhook handlers as if they were events from an arbitrary victim shop that has the app installed, obtaining that victim's Admin API access token inside the handler context (`session`, `admin` in `WebhookContextWithSession`).
- Cause the handler to execute business logic (e.g., data sync, uninstall cleanup, order/product mutations, deletion flows tied to `app/uninstalled`) against the victim shop's data using attacker-chosen payload bytes, since the attacker fully controls the raw body of their own legitimate webhook.
- DoS or corrupt state for the victim shop by spoofing topics such as `app/uninstalled` or by feeding malformed/unexpected payloads for a topic under the victim's identity, similar to how the original report describes overriding state for another entity and blocking legitimate delivery.

This is a cross-tenant impersonation / accepted-forged-request vulnerability directly reachable from an anonymous or low-privilege HTTP request to the app's public webhook endpoint.

### Likelihood Explanation
Exploitability requires: (1) the app is public/multi-tenant so the attacker can install it on their own shop and thus generate a validly-signed webhook body+HMAC pair, and (2) the attacker can deliver a raw HTTP POST to the app's webhook endpoint with a modified `X-Shopify-Shop-Domain` header (trivial, since these are plain unauthenticated headers on a normal HTTP request, and the endpoint is publicly reachable, not restricted to Shopify's IP ranges). Both preconditions are commonly true for real-world public Shopify apps built on `shopify-app-js`, making this practically exploitable rather than theoretical.

### Recommendation
Do not trust the shop-domain (or topic/webhook-id) header as an authenticated identity. At minimum:
- Reject webhook requests whose IP does not match Shopify's published webhook-delivery ranges, or
- Cross-validate the `domain` header against data that is actually part of the HMAC-signed payload (e.g., require the payload to embed the shop or verify against a previously stored mapping of `webhookId`/subscription to shop before trusting `domain`), or
- Track/deduplicate `webhookId` per registered subscription and shop so a replayed body cannot be attributed to a different shop than the one it was originally issued for.
At the very least, before calling `ensureValidOfflineSession(params, check.domain)`, confirm that a webhook with this exact `webhookId` was actually registered/expected for that domain.

### Proof of Concept
1. As a merchant, install the target public app on your own shop (`attacker-shop.myshopify.com`) and register a webhook subscription (e.g., `products/create`) to an HTTP endpoint you control.
2. Trigger the event (e.g., create a product) so Shopify sends the webhook to your endpoint; capture the raw body and the `X-Shopify-Hmac-Sha256` header value — this HMAC is valid because it's computed with the app's shared secret over the body only.
3. Replay the exact same raw body and `X-Shopify-Hmac-Sha256` header via a direct HTTP POST to the app's public webhook route, but set `X-Shopify-Shop-Domain: victim-shop.myshopify.com` (a shop that also has the app installed) and any desired `X-Shopify-Topic`/`X-Shopify-Webhook-Id`.
4. `api.webhooks.validate` (`packages/apps/shopify-api/lib/webhooks/validate.ts`) reports `valid: true` with `domain: 'victim-shop.myshopify.com'` because only the body HMAC is checked.
5. `authenticateWebhookFactory` calls `ensureValidOfflineSession(params, 'victim-shop.myshopify.com')`, obtaining the victim's offline session/access token, and invokes the registered handler with `session`/`admin` scoped to the victim shop, even though the event and payload originated from the attacker's own shop.

Note: I was not able to find or verify any explicit binding check elsewhere in the codebase between `webhookId`/subscription records and the `domain` header (index coverage may be incomplete for handler-registration code); if such a check exists in application-level code paths not indexed here, it would mitigate this specific exploitation path, but the shared library itself performs no such verification.

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L35-96)
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
