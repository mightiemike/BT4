Confirmed: this is a genuine analog of the reported bug class.

### Title
Webhook Domain Header Is Not Cryptographically Bound to the HMAC-Signed Body, Enabling Cross-Tenant Webhook Spoofing - (File: packages/apps/shopify-api/lib/webhooks/validate.ts)

### Summary
Shopify webhook authentication in `validateFactory` only computes the HMAC over the raw request body [1](#0-0) , while the shop identifier (`domain`) that determines *which tenant's session/access token* is used for the request is read verbatim from the unauthenticated `X-Shopify-Shop-Domain` header via `checkWebhooksHeaders`/`checkEventsHeaders` [2](#0-1) . This mirrors the reported `Account.claimReceipts()` bug: a security-critical identifier (`market`/here, `domain`) is never verified against the value embedded in the cryptographically-authenticated payload (`receipt.tracer`/here, the HMAC-signed body).

### Finding Description
`shopify.webhooks.validate` splits the trust decision into two independent, unlinked checks: (1) `validateHmacFromRequestFactory` verifies the `X-Shopify-Hmac-Sha256` (or events-hmac) header against the raw body using the app's single shared `apiSecretKey` [3](#0-2) ; (2) `checkWebhookHeaders` then independently extracts `domain` from a plain header with no cryptographic linkage to the HMAC-covered body [4](#0-3) . Because the app secret is shared across all shops that install the app (not per-tenant), any authenticated merchant who has installed the app can capture a legitimate `{rawBody, hmac}` pair from their own real webhook deliveries and re-POST it directly to the app's webhook endpoint with the `X-Shopify-Shop-Domain` header rewritten to a victim shop. The HMAC check still passes (it never inspected `domain`), so `check.valid === true` and `check.domain` is trusted as the victim shop.

Downstream, `authenticateWebhookFactory` (remix and react-router integrations) uses this unverified `check.domain` directly to load/attach the victim's offline session and access token: `ensureValidOfflineSession(params, check.domain)` [5](#0-4) , then builds an authenticated admin client scoped to that session [6](#0-5) . The same pattern exists in the react-router package [7](#0-6) .

### Impact Explanation
An attacker who legitimately installs the app on their own shop (an unprivileged, single-merchant actor — no leaked secrets, no MITM required) can trigger their own webhook (e.g., `products/create`) to obtain a valid `(rawBody, hmac)` pair, then replay that exact pair directly to the app's public webhook endpoint while forging the `domain` header to any victim shop that also has the app installed. Because `check.domain` is trusted to select the offline session, the attacker-controlled webhook body (topic/payload) is processed within the victim shop's authenticated context, and any app-side webhook handler logic that acts on payload data (e.g., writing to the victim's admin resources, billing decisions, orchestrating GraphQL mutations using `webhookContext.admin`) executes against the wrong tenant. This is a cross-tenant boundary violation directly analogous to the "malicious tracer market" scenario in the source report, where an unrelated (attacker-owned) trust context is used to act on a victim's protected resource.

### Likelihood Explanation
Likelihood is high for any app that (a) uses the shared webhook endpoint pattern shown in `shopify-app-remix`/`shopify-app-react-router` and (b) performs any state-changing or data-returning action keyed off `webhookContext.shop`/`webhookContext.session` inside the handler. No special privilege is required beyond installing the app as an ordinary merchant, which is the minimum bar for any multi-tenant Shopify app.

### Recommendation
Bind the shop identity to the authenticated payload instead of trusting an independent header: include the `domain`/shop value inside the HMAC-covered material (e.g., compute/verify the HMAC over `domain + rawBody`, or otherwise cryptographically tie the header to the signed body), or cross-check `domain` against another already-authenticated source (e.g., an existing offline session lookup keyed by a value derived from the signed payload) before trusting it to select tenant context — mirroring the report's recommendation to derive the market from the verified `receipt.tracer` rather than accepting an independent, unchecked parameter.

### Proof of Concept
1. Install the app on `attacker-shop.myshopify.com`; trigger a real webhook (e.g., create a product) to receive `{rawBody, X-Shopify-Hmac-Sha256: h}` from Shopify.
2. Send `POST /webhooks` directly to the app's public webhook endpoint with the same `rawBody` and `Hmac` header `h`, but set `X-Shopify-Shop-Domain: victim-shop.myshopify.com`.
3. `validateHmacFromRequestFactory` validates `h` against `rawBody` successfully (uses only the shared `apiSecretKey`) [3](#0-2) .
4. `checkWebhooksHeaders` returns `domain: 'victim-shop.myshopify.com'` unchanged [8](#0-7) .
5. `authenticate` loads the victim's offline session via `ensureValidOfflineSession(params, check.domain)` and processes the attacker-controlled `rawBody` payload in the victim's authenticated context [9](#0-8) .

### Citations

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L46-61)
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
```

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L89-134)
```typescript
function checkWebhookHeaders(
  headers: Headers,
  webhookType: WebhookTypeValue,
): WebhookValidationMissingHeaders | WebhookValidationValid {
  if (webhookType === WebhookType.Webhooks) {
    return checkWebhooksHeaders(headers);
  }
  return checkEventsHeaders(headers);
}

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L35-65)
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
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L88-102)
```typescript
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

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/webhooks/authenticate.ts (L52-102)
```typescript
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
