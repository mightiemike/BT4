### Title
Webhook HMAC only signs the raw body, leaving `X-Shopify-Shop-Domain`/`X-Shopify-Topic`/webhook headers unauthenticated, enabling cross-tenant webhook forgery - (File: `packages/apps/shopify-api/lib/webhooks/validate.ts`)

### Summary
`shopify.webhooks.validate()` verifies a webhook by computing an HMAC over the raw request body only. The shop domain, topic, webhook ID and API version that drive session lookup and business logic are read straight from request headers and are never bound into the signed data. Because the HMAC secret is the app's single Client Secret (shared across every shop that installs the app), any unprivileged merchant/customer who can get the app installed on their own store can capture one legitimately-signed `(body, hmac)` pair and replay it against the app's public webhook endpoint with a forged `X-Shopify-Shop-Domain`/`X-Shopify-Topic` header pointing at a completely different, victim shop and topic. This mirrors the CoWSwap `isValidSignature()` flaw: the signature only authenticates part of the payload (the body/appData-equivalent), while another field that materially changes downstream effect (the appData/shop-topic-equivalent) is left mutable and unchecked.

### Finding Description
`validateFactory` in `packages/apps/shopify-api/lib/webhooks/validate.ts` calls `validateHmacFromRequestFactory`, which only feeds `rawBody` into the HMAC check: [1](#0-0) 

After the body-only HMAC succeeds, `checkWebhookHeaders`/`checkWebhooksHeaders` extract `domain`, `topic`, `apiVersion`, `webhookId` purely from HTTP headers, with no cryptographic tie to the signed body: [2](#0-1) [3](#0-2) 

The consuming `authenticate.webhook` handler then uses the attacker-controllable `check.domain` directly to look up and vend an offline session/admin client, and `check.topic` to route business logic — both fields having zero relationship to what the HMAC actually signed: [4](#0-3) 

Since the HMAC secret (`apiSecretKey`) is the app's single Client Secret shared by all shop installations, an attacker only needs to install the target app on their **own** shop (an ordinary, unprivileged action) to obtain a legitimately-signed `(body, hmac)` pair for any topic they choose to trigger (e.g. `PRODUCTS_CREATE` with attacker-authored product JSON). They can then POST that exact `body` + `X-Shopify-Hmac-Sha256` to the app's webhook endpoint while substituting `X-Shopify-Shop-Domain` (and/or `X-Shopify-Topic`, `X-Shopify-Webhook-Id`) with values naming a different, victim shop that also has the app installed. `validateHmacFromRequestFactory` will report the HMAC as valid (it only checked the body), and `checkWebhookHeaders` will happily report the forged victim domain/topic as valid header data.

### Impact Explanation
This allows an unprivileged, single-merchant attacker to make the app believe a forged event happened for a **different tenant** (the victim shop), causing:
- `ensureValidOfflineSession(params, check.domain)` to load and use the **victim's** offline access token/admin API client while processing attacker-controlled body content.
- Topic confusion — the attacker chooses which topic-specific handler runs (`check.topic`) for the victim shop, independent of what body was actually signed, so a handler expecting one payload shape may act on attacker data associated with the victim's credentials.
- Depending on the app's webhook business logic (e.g. inventory sync, order creation/cancellation, GDPR/compliance handlers), this can lead to unauthorized state changes performed under the victim's stored access token, cross-tenant data corruption, or triggering of destructive/administrative topics against a shop the attacker does not control — directly analogous to the "loss of surplus" redirection in the ERC-1271 report, where a partially-checked signature let an adversary redirect trusted value to an unintended target.

### Likelihood Explanation
Likelihood is **Low-to-Medium**: it requires the attacker to (1) install the target public app on a shop they control (freely available to any Shopify merchant), (2) know/guess a victim shop domain that also runs the app, and (3) be able to reach the app's public webhook endpoint directly with a forged HTTP request bypassing Shopify's normal delivery path (which the code does not prevent, since there is no IP allow-listing or header-binding). No secret leakage or MITM is required — only the ability to become a legitimate customer/merchant of the app.

### Recommendation
Bind the header-derived, security-relevant fields (`shop domain`, `topic`, `webhook id`, `api version`) into the HMAC computation, or otherwise cryptographically associate them with the signed body — mirroring the report's own recommendation to make the app-data-equivalent immutable/verified rather than trusting an out-of-band field. At minimum, `checkWebhookHeaders`/`validateHmacFromRequestFactory` should reject a webhook if the shop domain in the header does not correspond to an actual pending/expected webhook delivery for that (body, hmac) pair, and apps should be encouraged/forced to validate that `check.domain` matches an install they expect before using it to select a session.

### Proof of Concept
1. Attacker installs the target public app on their own dev/test shop `attacker.myshopify.com` and triggers a `PRODUCTS_CREATE` webhook with an attacker-authored payload, capturing the resulting request: `rawBody = B`, `X-Shopify-Hmac-Sha256 = H` (valid because `H = HMAC(apiSecretKey, B)`).
2. Attacker crafts a new POST to the app's public webhook endpoint (e.g. `/webhooks`) reusing the exact same body `B` and header `X-Shopify-Hmac-Sha256: H`, but sets `X-Shopify-Shop-Domain: victim.myshopify.com` (a shop that also has the app installed) and, optionally, a different `X-Shopify-Topic`.
3. `validateHmacFromRequestFactory` recomputes `HMAC(apiSecretKey, B)` — matches `H`, so `validHmacResult.valid === true`.
4. `checkWebhookHeaders` extracts `domain = 'victim.myshopify.com'` and the (possibly forged) `topic` with no missing headers → returns `valid: true`.
5. `authenticateWebhookFactory` calls `ensureValidOfflineSession(params, 'victim.myshopify.com')`, loading the victim's stored offline session, and dispatches the app's handler for the (possibly attacker-chosen) topic using the attacker's body `B`, all authenticated as if Shopify itself had sent this webhook for the victim shop. [5](#0-4)

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
