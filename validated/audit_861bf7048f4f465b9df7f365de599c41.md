### Title
Webhook HMAC validation does not bind the signature to the `X-Shopify-Shop-Domain`/`X-Shopify-Topic` headers, enabling cross-tenant webhook replay - ([File: packages/apps/shopify-api/lib/webhooks/validate.ts])

### Summary
Shopify webhook authenticity is verified by `validateHmacFromRequestFactory`, which computes the HMAC over `rawBody` only, using the app's shared `apiSecretKey`. [1](#0-0)  The `X-Shopify-Shop-Domain` and `X-Shopify-Topic` headers, which determine *which tenant's* offline session is loaded and *which* webhook handler runs, are read separately and are never included in the signed material. [2](#0-1) 

### Finding Description
`validateFactory` first validates the request HMAC purely against `rawBody`, then independently extracts `domain`, `topic`, `webhookId`, etc. from headers via `checkWebhookHeaders`/`checkWebhooksHeaders`, with no cryptographic binding between the two. [3](#0-2)  Because the API secret is shared across all shops that installed the same app (it's the app's client secret, not a per-shop secret), a body+HMAC pair that Shopify legitimately generated for the attacker's own store is cryptographically valid for that same body **regardless of which `X-Shopify-Shop-Domain` or `X-Shopify-Topic` header accompanies it**.

Downstream, `authenticateWebhookFactory` (in shopify-app-remix and shopify-app-react-router) trusts `check.domain` directly to load the offline session: `const session = await ensureValidOfflineSession(params, check.domain);`, then hands that session (and an authenticated `admin` client bound to it) plus the topic and `JSON.parse(rawBody)` payload to the webhook handler. [4](#0-3)  The same pattern of using an unauthenticated header-derived shop/domain is present in the Express version, where `authCallback`/webhook processing is similarly built on `validate()`. [5](#0-4) 

This mirrors the report's root cause pattern precisely: a signature/authorization that was legitimately obtained for one context (Alice's `oTAP.permit`, here: Shopify's legitimate HMAC for the attacker's own shop's webhook body) can be replayed/front-run against an unintended target (Bob's exercise call, here: the victim shop's domain header) because the code never checks that the authorization is scoped to the actor/target actually being acted upon.

### Impact Explanation
An attacker who has installed the app on their own store can capture a legitimate webhook (body + valid HMAC) that Shopify sends them, then replay that exact body/HMAC pair to the app's webhook endpoint while substituting `X-Shopify-Shop-Domain` (and optionally `X-Shopify-Topic`) with a victim shop's domain. Since the HMAC check only validates `rawBody`, this forged request passes validation and causes the app to run its webhook handler using the **victim shop's offline access token/session** (`admin` client authenticated for the victim's store) with attacker-controlled JSON payload content and attacker-chosen topic label. Depending on the webhook handlers implemented by the app (e.g. `app/uninstalled`, order/customer data handlers, GDPR handlers), this can lead to unauthorized actions being taken against a shop the attacker does not own/operate, i.e., cross-tenant access via a forged accepted "Shopify" request.

### Likelihood Explanation
Requires the attacker to install the app on at least one shop (self-service, low-cost) and to observe a legitimate webhook trigger for that install, then send a forged HTTP POST — no secret key or admin credentials of the victim are needed. The likelihood is moderate: it depends on which webhook topics/handlers are registered and how "trusting" they are of the injected `session`/`shop` context, but the network-level primitive (header spoofing to redirect an otherwise-valid signature) is directly reachable by any external HTTP client with no privilege beyond installing the app once.

### Recommendation
Bind the webhook signature to the identifying headers, not just the body: include `X-Shopify-Shop-Domain`, `X-Shopify-Topic`, and `X-Shopify-Webhook-Id` as part of the HMAC input (or verify a Shopify-issued value that already encodes them, e.g. cross-check against the `webhookId`/subscription record fetched via the Admin API for the given domain) before trusting `check.domain` to select which tenant's session to load. At minimum, before calling `ensureValidOfflineSession(params, check.domain)`, verify that a stored session/webhook registration exists that ties this specific `webhookId` to `check.domain`, rather than trusting the header value implicitly once the body-only HMAC passes.

### Proof of Concept
1. Install the app on attacker-owned shop `attacker.myshopify.com`; trigger any subscribed webhook topic (e.g. `products/create`) to receive a real request with headers including a valid `X-Shopify-Hmac-Sha256` computed over the raw body using the app's `apiSecretKey`.
2. Capture the raw body and the `X-Shopify-Hmac-Sha256` value from that legitimate request.
3. Send a new POST request to the app's webhook endpoint with the **same** raw body and `X-Shopify-Hmac-Sha256`, but replace `X-Shopify-Shop-Domain` with `victim-shop.myshopify.com` and optionally change `X-Shopify-Topic`.
4. `validateHmacFromRequestFactory` recomputes the HMAC over the body only and it matches, so `validHmacResult.valid === true`. [6](#0-5) 
5. `checkWebhooksHeaders` extracts `domain: 'victim-shop.myshopify.com'` from the attacker-supplied header, unrelated to the HMAC. [2](#0-1) 
6. `authenticateWebhookFactory` loads and attaches the victim shop's offline session/`admin` client to the webhook context and invokes the corresponding handler as if Shopify itself sent this event for the victim shop. [7](#0-6)

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

**File:** packages/apps/shopify-app-express/src/webhooks/__tests__/process.test.ts (L86-104)
```typescript
  it('returns 401 on faulty webhook requests', async () => {
    const body = JSON.stringify({'test-body-received': true});

    const headers = {
      ...validWebhookHeaders(
        'TEST_TOPIC',
        body,
        shopify.api.config.apiSecretKey,
      ),
      'X-Shopify-Hmac-Sha256': 'invalid-hmac',
    };

    await request(app).post('/webhooks').set(headers).send(body).expect(401);

    expect(shopify.api.config.logger.log as jest.Mock).toHaveBeenCalledWith(
      LogSeverity.Error,
      expect.stringContaining('Could not validate request HMAC'),
    );
  });
```
