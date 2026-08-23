## Finding: Webhook HMAC Validation Doesn't Bind Signature to Shop Domain/Topic (Cross-Tenant Webhook Spoofing)

### Title
Webhook HMAC only authenticates the raw body, not the shop domain/topic headers, enabling cross-tenant webhook spoofing - (File: `packages/apps/shopify-api/lib/utils/hmac-validator.ts`, `packages/apps/shopify-api/lib/webhooks/validate.ts`)

### Summary
The Chainlink report's root cause is that data is accepted without verifying it is bound to the correct, current context (freshness/correctness checks on a data source that is otherwise trusted). The structurally equivalent pattern exists in the shopify-app-js webhook validation pipeline: the HMAC signature only authenticates the raw request body, while the shop domain and topic - which downstream code trusts implicitly to select a tenant's session and business logic path - are taken directly from unauthenticated headers.

### Finding Description
Webhook HMAC validation is computed only over the raw body: [1](#0-0) 

The `X-Shopify-Shop-Domain`, `X-Shopify-Topic`, and `X-Shopify-Webhook-Id` headers are only checked for presence, not cryptographically bound to the signed body: [2](#0-1) 

The webhook authentication flow then trusts `check.domain` (from the unauthenticated header) to load the offline session/access token and to route to the topic handler: [3](#0-2) 

Because the app-wide `apiSecretKey` is shared across every shop that installs the app, any merchant who receives a genuine webhook for their own store (body `B`, valid `hmac = HMAC(secret, B)`) can replay that exact `(B, hmac)` pair to the app's webhook endpoint while substituting a different `X-Shopify-Shop-Domain` (and/or `X-Shopify-Topic`) header pointing at another installed shop. Validation succeeds because the domain/topic are never part of the signed material, and the app then acts on the request as if it legitimately came from the target shop, using that shop's stored offline access token.

### Impact Explanation
This is a cross-tenant vulnerability class: a single unprivileged, authenticated app user (any merchant who has installed the app) can forge an "accepted" webhook that the framework treats as originating from a different, arbitrary merchant. Depending on what the app's webhook handlers do with the (attacker-controlled) payload, this can drive unauthorized writes/actions against another merchant's store using that merchant's real offline access token - a classic confused-deputy/cross-tenant access issue, not merely a theoretical replay.

### Likelihood Explanation
Exploitation requires only a normal merchant account with the app installed (no privileged actor, no secret leakage, no MITM) - the attacker legitimately receives HMAC-valid `(body, hmac)` pairs from Shopify for their own shop and only needs to modify unauthenticated headers on replay to the app's public webhook endpoint.

### Recommendation
Bind the shop domain and topic (and ideally webhook ID, for replay protection) into the authenticated material, e.g. by including them in the HMAC input, or by cross-checking that the resolved shop from the session/access-token record actually matches an out-of-band verified value, and by tracking `webhookId` to reject replays.

### Proof of Concept
1. Attacker installs the target app on their own controlled shop `attacker.myshopify.com`.
2. Attacker triggers an event (e.g., creates an order) to receive a legitimate, Shopify-signed webhook: body `B`, header `X-Shopify-Hmac-Sha256: H`, `X-Shopify-Shop-Domain: attacker.myshopify.com`.
3. Attacker resends the identical `POST` request to the app's webhook endpoint, keeping body `B` and header `H` unchanged, but sets `X-Shopify-Shop-Domain: victim.myshopify.com` (a shop that also has the app installed).
4. `validateHmacFromRequestFactory` validates `H` against `B` successfully (see `hmac-validator.ts:185-197`), and `checkWebhooksHeaders` accepts the forged domain header (`validate.ts:99-134`).
5. `authenticateWebhookFactory` loads `victim.myshopify.com`'s offline session/access token and invokes the app's webhook handler with `shop: victim.myshopify.com` and attacker-supplied payload `B` (`authenticate.ts:35-52`).

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L35-52)
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
```
