Based on my analysis, I found a strong analog. The `checkWebhookHeaders` function extracts `domain` directly from the `X-Shopify-Shop-Domain` HTTP header **without any binding to the HMAC signature**, since `validateHmacFromRequestFactory` computes the HMAC solely over the raw request body using the app's global `apiSecretKey` [1](#0-0) . The `domain` header value is never included in the signed payload [2](#0-1) , then this unauthenticated `domain` string is passed straight into `ensureValidOfflineSession` to resolve and load a session id, and ultimately build an authenticated Admin API client scoped to that shop [3](#0-2) .

### Title
Webhook HMAC Validation Does Not Bind Shop Domain, Allowing Cross-Tenant Session/Admin-Client Resolution via Header Spoofing - (File: packages/apps/shopify-api/lib/webhooks/validate.ts)

### Summary
The webhook authentication pipeline validates only that the raw request body's HMAC matches a signature computed with the app's single global `apiSecretKey`. The shop identifier (`X-Shopify-Shop-Domain`) used afterwards to resolve the offline session and build the authenticated Admin/GraphQL client is read from a header that is **not** part of the HMAC-signed data, exactly mirroring the Fractional finding where an unvalidated identifier (`_proposalId`) was trusted to resolve a security-critical value (`newVault`) without confirming it corresponded to the entity that was actually authorized.

### Finding Description
`validateHmacFromRequestFactory` computes and compares the HMAC using only `rawBody` and the shared `apiSecretKey` [1](#0-0) . `checkWebhookHeaders`/`checkWebhooksHeaders` then independently pull `domain` from the `X-Shopify-Shop-Domain` header and return it as trusted, only checking that it is *present*, not that it is cryptographically tied to the HMAC that was just verified [2](#0-1) . Because a single app's `apiSecretKey` is identical across every shop that has installed the app, and because the domain header is excluded from the signed material, a request whose body-HMAC was computed for shop A's payload can have its `X-Shopify-Shop-Domain` header rewritten to shop B, and `checkWebhooksHeaders` will still report `valid: true` with `domain: 'shop-B'`. Downstream, `authenticateWebhookFactory` uses this attacker-influenced `domain` to call `ensureValidOfflineSession(params, check.domain)`, which resolves shop B's offline session id via `getOfflineId` and loads shop B's stored access token, then constructs an authenticated Admin API client for shop B [4](#0-3) . This is the same root-cause pattern as the Fractional bug: an ID/identifier that resolves a target resource is accepted without verifying the resolved target matches what was actually authorized by the validated proof (the HMAC, analogous to the buyout `proposer` check).

### Impact Explanation
An attacker who can reach any of the app's webhook endpoints and who knows (or brute-forces/replays) one valid HMAC for *some* body belonging to *any* shop that installed the app can cause the app's webhook handler to load and act on a *different* shop's offline session/access token, resulting in cross-tenant session confusion — the webhook payload processed under the wrong shop's context, or (depending on app logic) actions taken against a shop the requester does not control, using that shop's own stored offline access token. This breaks the tenant isolation the webhook authentication is supposed to guarantee.

### Likelihood Explanation
Exploitation requires possession of a valid HMAC computed for the exact `rawBody` bytes being sent, since the endpoint is otherwise unauthenticated to the public internet. In multi-tenant public apps, shared/leaked webhook payload+HMAC pairs (e.g., via logs, debugging tools, replay of a previously observed/legitimate webhook whose body can be reused verbatim) are plausible without requiring a leaked `apiSecretKey`, and no MITM is needed — the header can simply be modified in a normal HTTP client since headers are attacker-controlled while only the body is protected.

### Recommendation
Bind the shop domain (and other identifying headers, e.g., topic/webhook ID) into the signed material verified against the HMAC, or otherwise cryptographically confirm that the `X-Shopify-Shop-Domain` header used to select which shop's session to load matches the shop the HMAC was actually generated for before calling `ensureValidOfflineSession`.

### Proof of Concept
1. Register/observe a legitimate webhook delivery for `shop-a.myshopify.com` with body `B` and its valid `X-Shopify-Hmac-Sha256` value `H` (computed over `B` with the app's `apiSecretKey`).
2. Replay a POST to the app's webhook endpoint with body `B`, header `X-Shopify-Hmac-Sha256: H` (unchanged, still valid since only `B` and the shared secret are hashed), but with `X-Shopify-Shop-Domain: shop-b.myshopify.com`.
3. `validateHmacFromRequestFactory` succeeds because `B` and `H` match [5](#0-4) .
4. `checkWebhooksHeaders` returns `valid: true, domain: 'shop-b.myshopify.com'` [6](#0-5) .
5. `authenticateWebhookFactory` calls `ensureValidOfflineSession(params, 'shop-b.myshopify.com')`, loading shop B's offline session/access token and constructing an Admin client scoped to shop B, even though the HMAC was never generated with shop B's data in mind [7](#0-6) .

### Citations

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L168-197)
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
