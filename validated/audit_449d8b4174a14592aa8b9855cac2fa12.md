### Title
Webhook shop-domain header is not covered by the HMAC signature, allowing cross-tenant admin-session binding - ([File: packages/apps/shopify-api/lib/webhooks/validate.ts])

### Summary
Shopify webhook authentication in this library validates the request's HMAC over the raw body only. The `X-Shopify-Shop-Domain` (and `X-Shopify-Topic`/`X-Shopify-Webhook-Id`) headers are read and trusted afterwards without being bound to the signature in any way. The `shopify-app-remix` / `shopify-app-react-router` webhook authenticators then use this unauthenticated `domain` header value to look up and attach that shop's **offline session and Admin API client** to the webhook context. An actor who controls one shop that has the app installed can capture a validly-HMAC'd webhook body (signed with the app's single shared `apiSecretKey`) and replay it to the webhook endpoint with a forged `X-Shopify-Shop-Domain` header pointing at a different shop, causing the app to process attacker-influenced data under another merchant's authenticated Admin session — the same class of "unvalidated identifier used to select a trusted resource" bug as the reported Superform issue, where the `superformId` was never checked against the entity it claimed to represent before its result was used for a security-relevant follow-up action.

### Finding Description
`validateHmacFromRequestFactory` computes the HMAC exclusively over `rawBody`: [1](#0-0) 

`webhooks.validate` calls this HMAC check and, once it passes, extracts `domain`, `topic`, and `webhookId` purely from HTTP headers with no cross-check against the signed body: [2](#0-1) [3](#0-2) 

The remix/react-router webhook authenticators then use `check.domain` directly to load the offline session and attach an authenticated Admin API client to the webhook context: [4](#0-3) [5](#0-4) 

Because `config.apiSecretKey` is a single secret shared by the app across *all* installing shops (not per-shop), any shop that has installed the app can receive a legitimately Shopify-signed webhook for its own store (e.g. by editing a product to trigger `PRODUCTS_UPDATE`), capture the `rawBody` + valid `X-Shopify-Hmac-Sha256` value, and replay that exact body/HMAC pair to the app's webhook endpoint while substituting the `X-Shopify-Shop-Domain` header with a different (victim) shop's domain. The HMAC check still passes because it only validates the body, and `checkWebhooksHeaders`/`checkEventsHeaders` accept the forged domain unconditionally. `ensureValidOfflineSession(params, check.domain)` will then load and return the victim shop's real offline session, and `adminClientFactory` binds an authenticated Admin API client for the victim shop to the webhook context — even though the payload content is fully attacker-controlled data from the attacker's own shop.

This mirrors the report's root cause precisely: an identifier supplied in the request (`superformId` there, the `domain` header here) is used to select and trust a privileged resource (a vault / an offline session+Admin client) without validating that the identifier is actually bound to the data that was cryptographically validated (the deposit amount there, the webhook body here).

### Impact Explanation
A single unprivileged actor who owns any shop that has installed the target app can cause the app's webhook handler code to execute with:
- `shop` = a victim merchant's domain,
- `session`/`admin` = the victim's real, authenticated offline Admin API session,
- `payload`/`topic` = fully attacker-controlled data from the attacker's own store.

Any app whose webhook handler acts on `payload` using `admin`/`session` (e.g., writing metafields, updating records keyed by IDs from the payload, triggering downstream Admin API mutations) will perform those actions against the wrong (victim) shop using the victim's real access token — a cross-tenant confusion/write primitive. The severity depends on what the specific app's webhook handler does with `admin`/`payload`, but the framework itself provides no barrier preventing this domain/session mismatch, unlike the OAuth callback flow (which binds `state` to a signed cookie) or the app-proxy flow (whose HMAC is computed over the full query including `shop`).

### Likelihood Explanation
Requires only: (1) attacker owns/controls one shop that has the target app installed (a normal, unprivileged merchant), and (2) the ability to trigger a webhook topic they can influence (e.g. editing their own store's product/order data) and capture the resulting request (via a local proxy or their own webhook receiver they can additionally register, or by observing app logs if available). No secret key or privileged role is required. This is a realistic, unprivileged-actor scenario matching the report's premise of a single actor crafting an unvalidated identifier.

### Recommendation
Bind the shop identity to the signed payload rather than trusting the `X-Shopify-Shop-Domain` header in isolation:
- Include the domain/shop (and ideally topic/webhook id) in the HMAC-covered material, or
- After loading the offline session for `check.domain`, verify that the session's shop is consistent with other trusted signals for the request (e.g., only accept sessions provisioned via this app's own OAuth for that literal domain, and add topic-specific payload validation that cross-checks resource ownership before using `admin`), or
- At minimum, document and enforce that webhook handlers must independently validate that `payload` fields belong to `shop` before performing privileged Admin API actions, and consider rejecting webhooks whose `domain` was not the one under which the current request's TLS/connection or app installation was established.

### Proof of Concept
1. Install the target app on shop A (attacker-controlled).
2. Trigger a `PRODUCTS_UPDATE` webhook by editing a product in shop A; capture the exact `rawBody` and the `X-Shopify-Hmac-Sha256` header Shopify sent to the app's webhook endpoint.
3. Confirm shop B (victim) also has the app installed (has a stored offline session), e.g. via other public information such as its shop domain being enumerable.
4. Replay the captured request to the app's webhook endpoint (`shopify.authenticate.webhook(request)` route), keeping `rawBody` and `X-Shopify-Hmac-Sha256` identical but replacing `X-Shopify-Shop-Domain: shop-a.myshopify.com` with `X-Shopify-Shop-Domain: shop-b.myshopify.com`.
5. Because HMAC validation in `validateHmacFromRequestFactory` only checks `rawBody` against the unchanged HMAC, `check.valid` is `true` and `check.domain` is `shop-b.myshopify.com`.
6. `ensureValidOfflineSession(params, check.domain)` loads shop B's real offline session; `adminClientFactory` returns an authenticated Admin client for shop B.
7. The app's webhook handler executes with `shop = shop-b.myshopify.com`, `admin` = shop B's Admin client, and `payload` = attacker's shop A product data — demonstrating cross-tenant session binding using forged headers.

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
